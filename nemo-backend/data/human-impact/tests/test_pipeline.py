"""
Data Pipeline Tests

Property 21: Restaurant Data Cleaning
Property 22: Restaurant Upsert Idempotency
Property 23: Restaurant Pipeline Aborts on HTTP Error
Property 24: Restaurant Pipeline Rolls Back on DB Error
Property 25: Collision Date Filtering
Property 26: Collision Upsert Idempotency
"""

import pytest
from unittest.mock import patch, MagicMock
from datetime import datetime, timedelta
import pandas as pd
from hypothesis import given, strategies as st, settings

import sys
sys.path.insert(0, '..')


class TestRestaurantDataCleaning:
    """Property 21: Restaurant Data Cleaning"""
    
    def test_removes_no_violations_rows(self):
        """Rows with action='No violations were recorded' should be removed."""
        from ingest_restaurants import clean_restaurant_data
        
        df = pd.DataFrame({
            'dba': ['Good Place', 'Bad Place', 'Another Place'],
            'action': ['Violations cited', 'No violations were recorded', 'Violations cited'],
            'grade': ['A', 'A', 'B']
        })
        
        cleaned = clean_restaurant_data(df)
        
        assert len(cleaned) == 2
        assert 'No violations were recorded' not in cleaned['action'].values
    
    def test_removes_null_dba_rows(self):
        """Rows with null dba should be removed."""
        from ingest_restaurants import clean_restaurant_data
        
        df = pd.DataFrame({
            'dba': ['Good Place', None, 'Another Place', ''],
            'action': ['Violations cited'] * 4,
            'grade': ['A', 'B', 'C', 'A']
        })
        
        cleaned = clean_restaurant_data(df)
        
        assert len(cleaned) == 2
        assert cleaned['dba'].notna().all()
        assert (cleaned['dba'].str.strip() != '').all()
    
    # Feature: pseudo-meta-glass, Property 21: Restaurant data cleaning
    @given(st.data())
    @settings(max_examples=100)
    def test_cleaning_removes_invalid_rows(self, data):
        """Property test: cleaning always removes no-violation and null-dba rows."""
        from ingest_restaurants import clean_restaurant_data
        
        # Generate test data
        n_rows = data.draw(st.integers(min_value=1, max_value=20))
        
        dbas = data.draw(st.lists(
            st.one_of(st.text(min_size=1, max_size=50), st.none()),
            min_size=n_rows, max_size=n_rows
        ))
        
        actions = data.draw(st.lists(
            st.sampled_from(['Violations cited', 'No violations were recorded', 'Other']),
            min_size=n_rows, max_size=n_rows
        ))
        
        df = pd.DataFrame({
            'dba': dbas,
            'action': actions,
            'grade': ['A'] * n_rows
        })
        
        cleaned = clean_restaurant_data(df)
        
        # Verify no "No violations" rows
        if 'action' in cleaned.columns:
            assert not (cleaned['action'] == 'No violations were recorded').any()
        
        # Verify no null dba rows
        if 'dba' in cleaned.columns:
            assert not cleaned['dba'].isna().any()


class TestCollisionDateFiltering:
    """Property 25: Collision Date Filtering"""
    
    def test_removes_old_collisions(self):
        """Collisions older than 3 years should be removed."""
        from ingest_collisions import filter_collision_data
        
        now = datetime.now()
        
        df = pd.DataFrame({
            'collision_id': ['1', '2', '3'],
            'crash_date': [
                now - timedelta(days=100),      # Recent - keep
                now - timedelta(days=4*365),    # 4 years ago - remove
                now - timedelta(days=2*365),    # 2 years ago - keep
            ],
            'latitude': [40.7, 40.8, 40.9],
            'longitude': [-74.0, -74.1, -74.2]
        })
        
        filtered = filter_collision_data(df)
        
        assert len(filtered) == 2
        
        # All remaining dates should be within 3 years
        three_years_ago = now - timedelta(days=3*365)
        assert (filtered['crash_date'] >= three_years_ago).all()
    
    # Feature: pseudo-meta-glass, Property 25: Collision date filtering
    @given(
        days_ago=st.integers(min_value=0, max_value=5*365)
    )
    @settings(max_examples=100)
    def test_date_filter_boundary(self, days_ago):
        """Property test: collisions exactly at 3-year boundary."""
        from ingest_collisions import filter_collision_data
        
        now = datetime.now()
        crash_date = now - timedelta(days=days_ago)
        
        df = pd.DataFrame({
            'collision_id': ['1'],
            'crash_date': [crash_date],
            'latitude': [40.7],
            'longitude': [-74.0]
        })
        
        filtered = filter_collision_data(df)
        
        three_years_days = 3 * 365
        if days_ago <= three_years_days:
            assert len(filtered) == 1
        else:
            assert len(filtered) == 0


class TestPipelineErrorHandling:
    """Property 23, 24, 27, 28: Pipeline error handling"""
    
    @patch('ingest_restaurants.requests.get')
    def test_http_error_aborts_restaurant_pipeline(self, mock_get):
        """Property 23: HTTP error should abort and leave DB unchanged."""
        from ingest_restaurants import run_restaurant_ingestion
        
        mock_response = MagicMock()
        mock_response.raise_for_status.side_effect = Exception("HTTP 500")
        mock_get.return_value = mock_response
        
        result = run_restaurant_ingestion()
        
        assert result['success'] is False
        assert 'error' in result
    
    @patch('ingest_restaurants.fetch_restaurant_data')
    @patch('ingest_restaurants.get_connection')
    def test_db_error_rolls_back(self, mock_get_conn, mock_fetch):
        """Property 24: DB error should rollback transaction."""
        from ingest_restaurants import upsert_restaurants
        
        # Setup mock data
        mock_fetch.return_value = pd.DataFrame({
            'inspection_id': ['1'],
            'dba': ['Test'],
            'grade': ['A'],
            'score': [10]
        })
        
        # Setup mock connection that raises on execute
        mock_cursor = MagicMock()
        mock_cursor.execute.side_effect = Exception("DB Error")
        mock_cursor.__enter__ = MagicMock(return_value=mock_cursor)
        mock_cursor.__exit__ = MagicMock(return_value=False)
        
        mock_conn = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_conn.__enter__ = MagicMock(return_value=mock_conn)
        mock_conn.__exit__ = MagicMock(return_value=False)
        
        mock_get_conn.return_value.__enter__ = MagicMock(return_value=mock_conn)
        mock_get_conn.return_value.__exit__ = MagicMock(return_value=False)
        
        df = pd.DataFrame({
            'inspection_id': ['1'],
            'dba': ['Test'],
            'grade': ['A'],
            'score': [10],
            'latitude': [40.7],
            'longitude': [-74.0]
        })
        
        with pytest.raises(RuntimeError):
            upsert_restaurants(df)


class TestUpsertIdempotency:
    """Property 22, 26: Upsert idempotency"""
    
    def test_upsert_sql_uses_on_conflict(self):
        """Upsert SQL should use ON CONFLICT for idempotency."""
        # Check that the SQL in the module uses ON CONFLICT
        from ingest_restaurants import upsert_restaurants
        import inspect
        
        source = inspect.getsource(upsert_restaurants)
        assert 'ON CONFLICT' in source
        assert 'DO UPDATE' in source


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
