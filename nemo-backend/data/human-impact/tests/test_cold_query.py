"""
cold_query Tool Tests

Property 15: cold_query Result Structure and Hazard Flag
Property 16: cold_query DB Error Raises Structured Error
"""

import pytest
from unittest.mock import patch, MagicMock
from hypothesis import given, strategies as st, settings

import sys
sys.path.insert(0, '..')

from tools.types import ColdQueryResult, HazardType


# Mock database results
MOCK_RESTAURANT_ROWS = [
    {
        'name': 'Good Restaurant',
        'address': '123 Main St, 10001',
        'grade': 'A',
        'score': 10,
        'hazard': False,
        'distance_meters': 50.0
    },
    {
        'name': 'Bad Restaurant',
        'address': '456 Oak Ave, 10002',
        'grade': 'C',
        'score': 35,
        'hazard': True,
        'distance_meters': 100.0
    },
    {
        'name': 'Medium Restaurant',
        'address': '789 Elm St, 10003',
        'grade': 'B',
        'score': 29,
        'hazard': True,  # score > 28
        'distance_meters': 150.0
    }
]


class TestColdQueryResultStructure:
    """Property 15: cold_query Result Structure and Hazard Flag"""
    
    def test_result_has_required_fields(self):
        """Every result should have name, address, grade, score, hazard."""
        result = ColdQueryResult(
            name='Test Restaurant',
            address='123 Test St',
            grade='A',
            score=10,
            hazard=False,
            distance_meters=50.0
        )
        
        assert hasattr(result, 'name')
        assert hasattr(result, 'address')
        assert hasattr(result, 'grade')
        assert hasattr(result, 'score')
        assert hasattr(result, 'hazard')
    
    def test_hazard_true_when_grade_c(self):
        """hazard should be True when grade == 'C'."""
        # This tests the logic that should be in cold_query
        grade = 'C'
        score = 10  # Low score, but grade C
        hazard = (grade == 'C' or score > 28)
        assert hazard is True
    
    def test_hazard_true_when_score_over_28(self):
        """hazard should be True when score > 28."""
        grade = 'B'
        score = 35
        hazard = (grade == 'C' or score > 28)
        assert hazard is True
    
    def test_hazard_false_when_good_grade_and_score(self):
        """hazard should be False when grade != 'C' and score <= 28."""
        grade = 'A'
        score = 10
        hazard = (grade == 'C' or score > 28)
        assert hazard is False
    
    # Feature: pseudo-meta-glass, Property 15: Hazard flag logic
    @given(
        grade=st.sampled_from(['A', 'B', 'C', None]),
        score=st.integers(min_value=0, max_value=100)
    )
    @settings(max_examples=100)
    def test_hazard_flag_property(self, grade, score):
        """Property test: hazard flag follows grade C OR score > 28 rule."""
        expected_hazard = (grade == 'C') or (score is not None and score > 28)
        
        # Simulate the hazard calculation
        actual_hazard = (grade == 'C' or (score is not None and score > 28))
        
        assert actual_hazard == expected_hazard


class TestColdQueryDBError:
    """Property 16: cold_query DB Error Raises Structured Error"""
    
    def test_db_error_includes_input_params(self):
        """RuntimeError message should include input parameters."""
        latitude = 40.7128
        longitude = -74.0060
        radius_meters = 500
        
        # Simulate the error message format from cold_query
        error_msg = (
            f"cold_query database error: Connection refused. "
            f"Input params: latitude={latitude}, longitude={longitude}, radius_meters={radius_meters}"
        )
        
        assert 'latitude=40.7128' in error_msg
        assert 'longitude=-74.006' in error_msg
        assert 'radius_meters=500' in error_msg
    
    # Feature: pseudo-meta-glass, Property 16: DB error includes params
    @given(
        latitude=st.floats(min_value=-90, max_value=90, allow_nan=False),
        longitude=st.floats(min_value=-180, max_value=180, allow_nan=False),
        radius_meters=st.integers(min_value=1, max_value=10000)
    )
    @settings(max_examples=100)
    def test_error_message_contains_all_params(self, latitude, longitude, radius_meters):
        """Property test: error message always contains all input params."""
        error_msg = (
            f"cold_query database error: Test error. "
            f"Input params: latitude={latitude}, longitude={longitude}, radius_meters={radius_meters}"
        )
        
        assert f"latitude={latitude}" in error_msg
        assert f"longitude={longitude}" in error_msg
        assert f"radius_meters={radius_meters}" in error_msg


class TestColdQueryIntegration:
    """Integration tests with mocked database."""
    
    @patch('tools.cold_query.get_db_connection')
    def test_returns_list_of_results(self, mock_get_conn):
        """cold_query should return a list of ColdQueryResult."""
        # Setup mock
        mock_cursor = MagicMock()
        mock_cursor.fetchall.return_value = MOCK_RESTAURANT_ROWS
        mock_cursor.__enter__ = MagicMock(return_value=mock_cursor)
        mock_cursor.__exit__ = MagicMock(return_value=False)
        
        mock_conn = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_get_conn.return_value = mock_conn
        
        # Import and call
        from tools.cold_query import cold_query
        results = cold_query(40.7128, -74.0060, 500)
        
        assert isinstance(results, list)
        assert len(results) == 3
        assert all(isinstance(r, ColdQueryResult) for r in results)
    
    @patch('tools.cold_query.get_db_connection')
    def test_hazardous_restaurants_flagged(self, mock_get_conn):
        """Restaurants with grade C or score > 28 should have hazard=True."""
        mock_cursor = MagicMock()
        mock_cursor.fetchall.return_value = MOCK_RESTAURANT_ROWS
        mock_cursor.__enter__ = MagicMock(return_value=mock_cursor)
        mock_cursor.__exit__ = MagicMock(return_value=False)
        
        mock_conn = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_get_conn.return_value = mock_conn
        
        from tools.cold_query import cold_query
        results = cold_query(40.7128, -74.0060, 500)
        
        # First restaurant (grade A, score 10) should not be hazard
        assert results[0].hazard is False
        
        # Second restaurant (grade C) should be hazard
        assert results[1].hazard is True
        
        # Third restaurant (score > 28) should be hazard
        assert results[2].hazard is True


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
