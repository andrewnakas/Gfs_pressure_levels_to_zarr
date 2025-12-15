# GFS Pressure Levels to Zarr

Automated system for downloading NOAA Global Forecast System (GFS) pressure level data and maintaining it in cloud-optimized Zarr format.

## Overview

This system runs every 6 hours via GitHub Actions to:
1. Download the latest GFS pressure level forecast data from NOAA
2. Convert GRIB2 files to Zarr format with compression
3. Publish to a dedicated `data` branch
4. Clean up old data (rolling dataset - only keeps latest forecast)

## Data Specifications

### Variables

Key meteorological variables at pressure levels:
- **gh**: Geopotential Height
- **t**: Temperature
- **u**: U-component of wind
- **v**: V-component of wind
- **r**: Relative Humidity
- **w**: Vertical Velocity

### Pressure Levels (hPa)

1, 2, 3, 5, 7, 10, 20, 30, 50, 70, 100, 150, 200, 250, 300, 350, 400, 450, 500, 550, 600, 650, 700, 750, 800, 850, 900, 925, 950, 975, 1000

### Forecast Configuration

- **GFS Cycles**: 00, 06, 12, 18 UTC (runs 4 times daily)
- **Forecast Hours**: 0-120 hours (every hour)
- **Grid Resolution**: 0.25° (default)
- **Update Schedule**: 03:30, 09:30, 15:30, 21:30 UTC
  - Offset 3.5 hours after GFS cycle times to ensure data availability

## Data Access

### From GitHub (Data Branch)

```python
import xarray as xr
import fsspec

# Open zarr store from GitHub
url = "github://andrewnakas/Gfs_pressure_levels_to_zarr/data/gfs-pressure-levels.zarr"
ds = xr.open_zarr(
    fsspec.get_mapper(url, ref={"branch": "data"}),
    consolidated=True
)

# Explore the dataset
print(ds)
```

### Local Usage

```python
import xarray as xr

# Clone the data branch
# git clone -b data https://github.com/andrewnakas/Gfs_pressure_levels_to_zarr.git

# Open local zarr
ds = xr.open_zarr('gfs-pressure-levels.zarr', consolidated=True)

# Select specific pressure level and variable
temp_500mb = ds['t'].sel(isobaricInhPa=500)

# Select specific forecast hour
temp_48h = ds['t'].sel(step=48)
```

## Manual Execution

Run the update script locally:

```bash
# Install dependencies
pip install -r requirements.txt

# Install system dependencies (Ubuntu/Debian)
sudo apt-get install libeccodes-dev libeccodes-tools

# Run update
python scripts/update_gfs_zarr.py
```

## GitHub Actions Workflow

The workflow runs automatically on schedule and can be triggered manually:

- **Scheduled**: Every 6 hours at :30 past the hour
- **Manual**: Via GitHub Actions UI (workflow_dispatch)
- **On Push**: Automatically tests on pushes to `claude/**` branches

### Workflow Steps

1. Checkout code
2. Setup Python 3.11
3. Install dependencies (system + Python)
4. Download and process GFS data
5. Commit to `data` branch (force push to keep history clean)
6. Upload data summary artifact

## Architecture Decisions

### Why Zarr?

- **Cloud-optimized**: Efficient partial reads without downloading entire dataset
- **Compression**: Zstd level 3 compression reduces storage significantly
- **Chunking**: Optimized chunks for common access patterns
- **Self-describing**: Metadata embedded in the format

### Why Rolling Dataset?

- Keeps only the latest forecast initialization
- Prevents unlimited storage growth
- GitHub repository stays manageable
- Users typically only need current forecast

### Why Separate Data Branch?

- Keeps code branch clean and fast
- Git LFS not required
- Easy to force-push without affecting code history
- Clear separation of concerns

## Troubleshooting

### Common Issues

**cfgrib errors with mixed vertical levels**:
- Script filters for `typeOfLevel: 'isobaricInhPa'` to avoid conflicts
- Only processes pressure level variables

**404 errors for recent cycles**:
- Script automatically targets cycle from 6 hours ago
- Data typically available 3-4 hours after cycle time

**Large file sizes**:
- Zarr compression (zstd) significantly reduces size
- First 120 forecast hours only to manage storage
- Grid resolution set to 0.25° (can be adjusted if needed)

## Development

### Project Structure

```
.
├── .github/
│   └── workflows/
│       └── update-gfs-data.yml    # GitHub Actions workflow
├── scripts/
│   └── update_gfs_zarr.py         # Main update script
├── requirements.txt                # Python dependencies
├── .gitignore                      # Git ignore patterns
└── README.md                       # This file
```

### Contributing

1. Create a feature branch from `main`
2. Make changes
3. Test locally: `python scripts/update_gfs_zarr.py`
4. Commit and push
5. Open pull request

## References

- [NOAA GFS Data](https://registry.opendata.aws/noaa-gfs-bdp-pds/)
- [Zarr Format](https://zarr.readthedocs.io/)
- [xarray Documentation](https://docs.xarray.dev/)
- [cfgrib Documentation](https://github.com/ecmwf/cfgrib)

## License

This project follows the data from NOAA which is in the public domain.

## Credits

Inspired by [NBM_to_zarr](https://github.com/andrewnakas/Nbm_to_zarr) project structure.
