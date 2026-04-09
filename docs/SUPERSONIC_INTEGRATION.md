# SuperSonic Data Integration with Superset

This guide explains how to import SuperSonic demo data into Superset for visualization.

## Overview

SuperSonic and Superset can share the same underlying database. This integration allows you to:

1. Use SuperSonic for natural language queries
2. Use Superset for rich visualizations and dashboards
3. Share the same data source between both platforms

## Prerequisites

### 1. Database Setup

Make sure you have a database with SuperSonic demo tables. The following tables are supported:

**S2VisitsDemo:**
- `s2_user_department` - User to department mapping
- `s2_pv_uv_statis` - Page view/UV statistics
- `s2_stay_time_statis` - Stay time statistics

**S2ArtistDemo:**
- `singer` - Singer information
- `genre` - Music genre information

**S2CompanyDemo:**
- `company` - Company information
- `brand` - Brand information
- `brand_revenue` - Brand revenue data

### 2. Superset Configuration

Ensure Superset is running and properly configured:

```bash
# Start Superset
cd /path/to/superset
superset run -h 0.0.0.0 -p 8088 --with-threads --reload --debugger
```

### 3. Database Connection

Add your database connection in Superset:

1. Go to **Data** → **Databases**
2. Click **+ Database**
3. Enter connection details (e.g., MySQL, PostgreSQL)
4. Test connection and save

## Quick Start

### Method 1: Using the Import Script (Recommended)

```bash
# From Superset root directory
cd /path/to/superset

# Import SuperSonic data
python scripts/import_supersonic_data.py

# Or specify a custom database name
python scripts/import_supersonic_data.py --database my_supersonic_db

# For verbose output
python scripts/import_supersonic_data.py --verbose
```

### Method 2: Using Python Shell

```bash
# Start Superset shell
superset shell

# Then run:
from superset.examples import supersonic
supersonic.load_supersonic_data()
```

### Method 3: Using Flask Shell

```bash
# From Superset root directory
flask shell

# Then run:
from superset.examples import supersonic
supersonic.load_supersonic_data(database_name="your_database_name")
```

## What Gets Created

After successful import, you will have:

### Datasets (SqlaTable)
- `s2_pv_uv_statis` - With metrics: `count`
- `s2_stay_time_statis` - With metrics: `sum__stay_hours`, `avg__stay_hours`
- `singer` - With metrics: `sum__js_play_cnt`, `sum__down_cnt`, `sum__favor_cnt`
- `company` - With metrics: `sum__annual_turnover`, `avg__employee_count`
- `brand` - With metrics: `sum__registered_capital`
- `brand_revenue` - With metrics: `sum__revenue`, `sum__profit`
- `genre` - Dimension only
- `s2_user_department` - Dimension only

### Charts
1. **Total Page Views** (Big Number) - Total count of page views
2. **Page Views Trend** (Line Chart) - Page views over time
3. **Page Popularity Ranking** (Bar Chart) - Most viewed pages
4. **Page Views by Department** (Table) - Page views grouped by user
5. **Average Stay Time** (Big Number) - Average hours per visit
6. **Singer Play Statistics** (Bar Chart) - Top singers by play count
7. **Brand Revenue Trend** (Line Chart) - Revenue over time
8. **Company Revenue Comparison** (Pie Chart) - Revenue by brand
9. **Brand Profit Analysis** (Area Chart) - Profit trends

### Dashboard
- **SuperSonic Data Visualization** - Pre-configured dashboard with all charts

## Accessing the Dashboard

After import, access the dashboard at:

```
http://localhost:8088/superset/dashboard/supersonic_data/
```

## Advanced Usage

### Custom Database Name

If your SuperSonic tables are in a specific database:

```python
from superset.examples import supersonic
supersonic.load_supersonic_data(database_name="super_sonic_db")
```

### Metadata Only (Faster)

To create datasets without fetching table statistics:

```python
supersonic.load_supersonic_dashboard(only_metadata=True)
```

### Force Refresh

To refresh all metadata:

```python
supersonic.load_supersonic_dashboard(force=True)
```

## Troubleshooting

### Tables Not Found

If you see warnings about tables not found:

1. Verify the database connection is working
2. Check that tables exist in the database:
   ```sql
   SHOW TABLES;
   -- or
   SELECT table_name FROM information_schema.tables
   WHERE table_schema = 'your_database';
   ```
3. Make sure you're using the correct database name

### Permission Issues

If you encounter permission issues:

```bash
# Make the script executable
chmod +x scripts/import_supersonic_data.py
```

### Dashboard Not Appearing

1. Check the logs for errors
2. Verify slices were created in the database
3. Try refreshing the Superset UI

## File Structure

```
superset/
├── superset/examples/supersonic.py       # Main integration module
├── scripts/import_supersonic_data.py     # Standalone import script
└── docs/SUPERSONIC_INTEGRATION.md        # This documentation
```

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                    SuperSonic Demo Database                 │
│  (MySQL/PostgreSQL/H2)                                      │
├─────────────────────────────────────────────────────────────┤
│  Tables: s2_pv_uv_statis, singer, company, brand_revenue...  │
└─────────────────────────────────────────────────────────────┘
                          │
          ┌───────────────┴───────────────┐
          ▼                               ▼
┌─────────────────────┐         ┌─────────────────────┐
│    SuperSonic       │         │     Superset        │
│  - Natural Language │         │  - Visualizations   │
│  - Semantic Layer   │         │  - Dashboards       │
│  - Chat BI          │         │  - SQL Lab          │
└─────────────────────┘         └─────────────────────┘
```

## Next Steps

1. **Customize Charts**: Edit the imported charts in Superset UI
2. **Create New Dashboards**: Use the imported datasets for custom visualizations
3. **Add SQL Queries**: Use SQL Lab to query the SuperSonic tables
4. **Set Automatic Refresh**: Configure dashboard refresh intervals

## Contributing

To add support for additional SuperSonic tables:

1. Edit `superset/examples/supersonic.py`
2. Add table configuration to `SUPERSONIC_TABLES`
3. Create corresponding chart in `create_supersonic_slices()`
4. Update dashboard layout in `SUPERSONIC_DASHBOARD_POSITIONS`

## License

This integration follows the same license as Apache Superset (Apache 2.0).

SuperSonic is a separate project: https://github.com/tencentmusic/super sonic
