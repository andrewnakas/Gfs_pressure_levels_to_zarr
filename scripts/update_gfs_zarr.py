#!/usr/bin/env python3
"""
GFS Pressure Levels to Zarr - Operational Update Script

Downloads latest GFS pressure level forecasts from NOAA and maintains
a rolling Zarr dataset. Designed to run continuously via GitHub Actions.
"""

import os
import sys
import logging
from datetime import datetime, timedelta
from pathlib import Path
import xarray as xr
import zarr
import s3fs
import shutil
import tempfile

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Configuration
ZARR_PATH = Path("data/gfs-pressure-levels.zarr")
S3_BUCKET = "noaa-gfs-bdp-pds"

# GFS pressure levels (hPa)
PRESSURE_LEVELS = [
    1, 2, 3, 5, 7, 10, 20, 30, 50, 70, 100, 150, 200, 250, 300, 350,
    400, 450, 500, 550, 600, 650, 700, 750, 800, 850, 900, 925, 950,
    975, 1000
]

# Key meteorological variables at pressure levels
VARIABLES = {
    'gh': 'Geopotential Height',
    't': 'Temperature',
    'u': 'U-component of wind',
    'v': 'V-component of wind',
    'r': 'Relative Humidity',
    'w': 'Vertical Velocity',
}

# Forecast hours - GFS runs 4 times daily (00, 06, 12, 18 UTC)
# 0-120h every hour, 120-384h every 3 hours
FORECAST_HOURS = list(range(0, 121, 1)) + list(range(123, 385, 3))

# Grid resolution (0.25 degree is default for GFS)
GRID_RES = "0p25"


def get_latest_gfs_cycle(max_age_hours=6):
    """
    Determine the most recent available GFS cycle.

    GFS cycles run at 00, 06, 12, 18 UTC. Data typically available
    3-4 hours after cycle time.

    Args:
        max_age_hours: Maximum age to look back for available data

    Returns:
        datetime: The cycle time to download
    """
    now = datetime.utcnow()

    # Round down to nearest 6-hour cycle
    cycle_hour = (now.hour // 6) * 6
    cycle_time = now.replace(hour=cycle_hour, minute=0, second=0, microsecond=0)

    # Account for processing delay - go back one cycle to ensure data is ready
    cycle_time = cycle_time - timedelta(hours=6)

    logger.info(f"Target GFS cycle: {cycle_time.strftime('%Y%m%d %H UTC')}")
    return cycle_time


def check_gfs_file_exists(s3, cycle_time, forecast_hour, level):
    """
    Check if a specific GFS file exists on S3.

    Args:
        s3: S3 filesystem object
        cycle_time: Forecast initialization time
        forecast_hour: Forecast lead time
        level: Pressure level

    Returns:
        str or None: S3 path if exists, None otherwise
    """
    date_str = cycle_time.strftime("%Y%m%d")
    hour_str = cycle_time.strftime("%H")
    fhour_str = f"{forecast_hour:03d}"

    # GFS file naming: gfs.YYYYMMDD/HH/atmos/gfs.tHHz.pgrb2.0p25.fFFF
    s3_path = f"{S3_BUCKET}/gfs.{date_str}/{hour_str}/atmos/gfs.t{hour_str}z.pgrb2.{GRID_RES}.f{fhour_str}"

    try:
        if s3.exists(s3_path):
            return s3_path
    except Exception as e:
        logger.debug(f"Error checking {s3_path}: {e}")

    return None


def download_and_convert_cycle(cycle_time, output_path):
    """
    Download GFS data for a specific cycle and convert to Zarr.

    Args:
        cycle_time: Forecast initialization datetime
        output_path: Path to output Zarr store
    """
    logger.info(f"Processing GFS cycle: {cycle_time}")

    # Initialize S3 filesystem (anonymous access to public bucket)
    s3 = s3fs.S3FileSystem(anon=True)

    datasets = []
    successful_hours = []

    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir = Path(tmpdir)

        for fhour in FORECAST_HOURS[:121]:  # Start with first 120 hours
            logger.info(f"Processing forecast hour {fhour:03d}")

            # Check if file exists
            s3_path = check_gfs_file_exists(s3, cycle_time, fhour, None)

            if not s3_path:
                logger.warning(f"File not found for forecast hour {fhour}, skipping")
                continue

            try:
                # Open with S3 filesystem - cfgrib can read directly from S3
                with s3.open(s3_path, 'rb') as f:
                    # Read into temporary file to avoid cfgrib issues
                    tmp_grib = tmpdir / f"gfs_f{fhour:03d}.grib2"
                    with open(tmp_grib, 'wb') as out:
                        out.write(f.read())

                # Open with xarray and cfgrib
                # Filter for pressure level variables only
                ds = xr.open_dataset(
                    tmp_grib,
                    engine='cfgrib',
                    backend_kwargs={
                        'filter_by_keys': {'typeOfLevel': 'isobaricInhPa'},
                        'indexpath': ''
                    }
                )

                # Add time coordinates
                ds = ds.assign_coords({
                    'time': cycle_time,
                    'step': fhour
                })

                # Select only our desired variables and levels
                var_list = [v for v in VARIABLES.keys() if v in ds.data_vars]
                if var_list:
                    ds = ds[var_list]

                    # Filter pressure levels if needed
                    if 'isobaricInhPa' in ds.dims:
                        available_levels = [l for l in PRESSURE_LEVELS if l in ds.isobaricInhPa.values]
                        ds = ds.sel(isobaricInhPa=available_levels)

                    datasets.append(ds)
                    successful_hours.append(fhour)
                    logger.info(f"Successfully processed forecast hour {fhour}")
                else:
                    logger.warning(f"No valid variables found for hour {fhour}")

                # Cleanup temp file
                tmp_grib.unlink()

            except Exception as e:
                logger.error(f"Error processing forecast hour {fhour}: {e}")
                continue

    if not datasets:
        raise RuntimeError("No valid forecast data could be downloaded")

    logger.info(f"Successfully downloaded {len(datasets)} forecast hours")

    # Concatenate along time/step dimension
    logger.info("Concatenating datasets...")
    combined = xr.concat(datasets, dim='step')

    # Add metadata
    combined.attrs.update({
        'title': 'GFS Pressure Level Forecast',
        'institution': 'NOAA/NCEP',
        'source': 'Global Forecast System (GFS)',
        'initialization_time': cycle_time.isoformat(),
        'created': datetime.utcnow().isoformat(),
        'grid_resolution': GRID_RES,
        'forecast_hours': successful_hours
    })

    # Prepare for Zarr output with compression
    logger.info("Writing to Zarr...")

    # Define encoding for compression
    encoding = {}
    for var in combined.data_vars:
        encoding[var] = {
            'compressor': zarr.Blosc(cname='zstd', clevel=3),
            'chunks': {
                'step': 1,
                'isobaricInhPa': len(PRESSURE_LEVELS) if 'isobaricInhPa' in combined[var].dims else 1,
                'latitude': 180,
                'longitude': 360
            }
        }

    # Remove old zarr store if exists
    if output_path.exists():
        logger.info(f"Removing old Zarr store at {output_path}")
        shutil.rmtree(output_path)

    # Create parent directory
    output_path.parent.mkdir(parents=True, exist_ok=True)

    # Write to Zarr
    combined.to_zarr(
        output_path,
        mode='w',
        consolidated=True,
        encoding=encoding
    )

    logger.info(f"Successfully wrote Zarr store to {output_path}")

    # Log size
    total_size = sum(f.stat().st_size for f in output_path.rglob('*') if f.is_file())
    logger.info(f"Zarr store size: {total_size / 1e9:.2f} GB")

    return combined


def main():
    """Main execution function."""
    try:
        logger.info("=" * 60)
        logger.info("GFS Pressure Levels to Zarr - Starting Update")
        logger.info("=" * 60)

        # Determine latest cycle
        cycle_time = get_latest_gfs_cycle()

        # Download and convert
        dataset = download_and_convert_cycle(cycle_time, ZARR_PATH)

        logger.info("=" * 60)
        logger.info("Update completed successfully!")
        logger.info(f"Dataset shape: {dict(dataset.dims)}")
        logger.info(f"Variables: {list(dataset.data_vars)}")
        logger.info("=" * 60)

        return 0

    except Exception as e:
        logger.error(f"Fatal error during update: {e}", exc_info=True)
        return 1


if __name__ == "__main__":
    sys.exit(main())
