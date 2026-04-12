"""
Data Pipeline Scheduler

Runs nightly batch ingestion of cold datasets at 02:00 UTC
and periodic refresh of hot/cold hybrid data (311 complaints).

Uses APScheduler with CronTrigger for scheduling.
"""

import os
import sys
import logging
import signal
from datetime import datetime

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

import sys
sys.path.insert(0, '..')
from shared.db import init_pool, close_pool, test_connection
from ingesters.ingest_restaurants import run_restaurant_ingestion
from ingesters.ingest_collisions import run_collision_ingestion
from ingesters.ingest_wikidata_productions import run as run_wikidata_ingestion
from ingesters.ingest_311 import run_311_ingestion

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def validate_environment() -> bool:
    """
    Validate required environment variables are present.
    
    Returns:
        True if all required vars present, False otherwise
    """
    required_vars = [
        'POSTGRES_HOST',
        'POSTGRES_PORT',
        'POSTGRES_USER',
        'POSTGRES_PASSWORD',
        'POSTGRES_DB',
    ]
    
    missing = [var for var in required_vars if not os.environ.get(var)]
    
    if missing:
        logger.fatal(f"Missing required environment variables: {missing}")
        return False
    
    return True


def nightly_cold_ingestion():
    """
    Run nightly ingestion of cold datasets.
    Scheduled for 02:00 UTC.
    """
    logger.info("=" * 60)
    logger.info("Starting nightly cold data ingestion")
    logger.info("=" * 60)
    
    start_time = datetime.now()
    results = {}
    
    # Restaurant inspections
    logger.info("--- Restaurant Ingestion ---")
    results['restaurants'] = run_restaurant_ingestion()
    
    # Motor vehicle collisions
    logger.info("--- Collision Ingestion ---")
    results['collisions'] = run_collision_ingestion()
    try:
        results['wikidata'] = run_wikidata_ingestion()
    except Exception as e:
        logger.error(f'Wikidata ingestion failed: {e}')
    
    elapsed = (datetime.now() - start_time).total_seconds()
    
    logger.info("=" * 60)
    logger.info(f"Nightly ingestion complete in {elapsed:.1f}s")
    for dataset, result in results.items():
        status = "✓" if result.get('success') else "✗"
        logger.info(f"  {status} {dataset}: {result}")
    logger.info("=" * 60)


def periodic_311_refresh():
    """
    Periodic refresh of 311 complaints (hot/cold hybrid).
    Runs every 15 minutes to keep complaint data fresh.
    """
    logger.info("Refreshing 311 complaint data")
    result = run_311_ingestion(hours_back=24)
    
    if result.get('success'):
        logger.info(f"311 refresh complete: {result.get('upserted_count', 0)} records")
    else:
        logger.error(f"311 refresh failed: {result.get('error')}")


def startup_ingestion():
    """
    Run initial data ingestion on startup.
    Ensures database has data before scheduler takes over.
    """
    logger.info("Running startup data ingestion")
    
    # Run all ingestions
    nightly_cold_ingestion()
    periodic_311_refresh()
    
    logger.info("Startup ingestion complete")


def shutdown_handler(signum, frame):
    """Handle graceful shutdown."""
    logger.info("Received shutdown signal, cleaning up...")
    close_pool()
    sys.exit(0)


def main():
    """Main entry point for the data pipeline."""
    logger.info("=" * 60)
    logger.info("pseudoMetaGlass Data Pipeline Starting")
    logger.info("=" * 60)
    
    # Validate environment
    if not validate_environment():
        sys.exit(1)
    
    # Initialize database pool
    try:
        init_pool()
        if not test_connection():
            logger.fatal("Database connection test failed")
            sys.exit(1)
        logger.info("Database connection established")
    except Exception as e:
        logger.fatal(f"Failed to initialize database: {e}")
        sys.exit(1)
    
    # Register signal handlers
    signal.signal(signal.SIGTERM, shutdown_handler)
    signal.signal(signal.SIGINT, shutdown_handler)
    
    # Get cron schedule from environment
    cron_hour = int(os.environ.get('PIPELINE_CRON_HOUR', '2'))
    cron_minute = int(os.environ.get('PIPELINE_CRON_MINUTE', '0'))
    
    # Create scheduler
    scheduler = BlockingScheduler()
    
    # Schedule nightly cold data ingestion
    scheduler.add_job(
        nightly_cold_ingestion,
        trigger=CronTrigger(hour=cron_hour, minute=cron_minute, timezone='UTC'),
        id='nightly_cold_ingestion',
        name='Nightly Cold Data Ingestion',
        replace_existing=True
    )
    logger.info(f"Scheduled nightly ingestion at {cron_hour:02d}:{cron_minute:02d} UTC")
    
    # Schedule periodic 311 refresh (every 15 minutes)
    scheduler.add_job(
        periodic_311_refresh,
        trigger=IntervalTrigger(minutes=15),
        id='periodic_311_refresh',
        name='Periodic 311 Refresh',
        replace_existing=True
    )
    logger.info("Scheduled 311 refresh every 15 minutes")
    
    # Run startup ingestion
    try:
        startup_ingestion()
    except Exception as e:
        logger.error(f"Startup ingestion failed: {e}")
        # Continue anyway - scheduler will retry
    
    # Start scheduler
    logger.info("Data pipeline scheduler started")
    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        logger.info("Scheduler stopped")
    finally:
        close_pool()


if __name__ == '__main__':
    main()
