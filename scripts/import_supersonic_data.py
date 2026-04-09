#!/usr/bin/env python3
# Licensed to the Apache Software Foundation (ASF) under one
# or more contributor license agreements.  See the NOTICE file
# distributed with this work for additional information
# regarding copyright ownership.  The ASF licenses this file
# to you under the Apache License, Version 2.0 (the
# "License"); you may not use this file except in compliance
# with the License.  You may obtain a copy of the License at
#
#   http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing,
# software distributed under the License is distributed on an
# "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY
# KIND, either express or implied.  See the License for the
# specific language governing permissions and limitations
# under the License.
"""
SuperSonic Data Import Script for Superset

This script imports SuperSonic demo data into Superset for visualization.

Usage:
    # From Superset root directory
    python scripts/import_supersonic_data.py

    # Or specify a custom database
    python scripts/import_supersonic_data.py --database my_supersonic_db

Requirements:
    1. SuperSonic demo tables must exist in the database
    2. Superset must be properly configured
    3. Database connection must be established
"""
import argparse
import logging
import os
import sys

# Add the Superset app to the path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Setup logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)


def main():
    """Main entry point for the import script."""
    parser = argparse.ArgumentParser(
        description="Import SuperSonic demo data into Superset for visualization"
    )
    parser.add_argument(
        "--database",
        "-d",
        type=str,
        default=None,
        help="Name of the database where SuperSonic tables exist (default: example database)",
    )
    parser.add_argument(
        "--only-metadata",
        action="store_true",
        help="Only create metadata without fetching table statistics",
    )
    parser.add_argument(
        "--force",
        "-f",
        action="store_true",
        help="Force refresh of all metadata",
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Enable verbose logging",
    )

    args = parser.parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    try:
        # Initialize Superset app
        from superset.app import create_app

        app = create_app()

        with app.app_context():
            from superset.examples import supersonic

            logger.info("=" * 60)
            logger.info("SuperSonic Data Import for Superset")
            logger.info("=" * 60)

            if args.database:
                logger.info(f"Using database: {args.database}")
            else:
                logger.info("Using example database")

            # Import the data
            supersonic.load_supersonic_dashboard(
                database_name=args.database,
                only_metadata=args.only_metadata,
                force=args.force,
            )

            logger.info("=" * 60)
            logger.info("Import completed successfully!")
            logger.info("=" * 60)
            logger.info("You can now view the SuperSonic dashboard in Superset:")
            logger.info("  URL: http://localhost:8088/superset/dashboard/supersonic_data/")
            logger.info("=" * 60)

    except Exception as e:
        logger.error(f"Import failed: {e}")
        import traceback

        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
