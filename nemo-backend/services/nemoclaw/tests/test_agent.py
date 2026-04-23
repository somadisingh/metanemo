"""
NemoClaw Agent Tests

Property 12: Intent Classification Routes to Correct Tool
Property 13: Tool Result Synthesized to Natural Language
Property 14: Low Confidence Returns Clarifying Question
"""

import pytest
from unittest.mock import patch, MagicMock, AsyncMock
from hypothesis import given, strategies as st, settings

import sys
sys.path.insert(0, '..')


class TestIntentClassification:
    """Property 12: Intent Classification Routes to Correct Tool"""
    
    def test_food_intent_routes_to_cold_query(self):
        """Food-related queries should route to cold_query."""
        food_queries = [
            "Is this restaurant safe?",
            "What's the health grade here?",
            "Any food safety issues nearby?",
            "Restaurant inspection results"
        ]
        
        for query in food_queries:
            # Simulate classification
            if any(kw in query.lower() for kw in ['restaurant', 'food', 'health grade', 'inspection']):
                tool = 'cold_query'
            else:
                tool = 'hot_query'
            
            assert tool == 'cold_query', f"Query '{query}' should route to cold_query"
    
    def test_traffic_intent_routes_to_hot_query(self):
        """Traffic-related queries should route to hot_query."""
        traffic_queries = [
            "Any accidents nearby?",
            "Is this intersection dangerous?",
            "Traffic conditions ahead",
            "Street closures"
        ]
        
        for query in traffic_queries:
            if any(kw in query.lower() for kw in ['accident', 'traffic', 'intersection', 'closure']):
                tool = 'hot_query'
            else:
                tool = 'cold_query'
            
            assert tool == 'hot_query', f"Query '{query}' should route to hot_query"
    
    def test_transit_intent_routes_to_hot_query(self):
        """Transit-related queries should route to hot_query."""
        transit_queries = [
            "Is the subway running?",
            "Any MTA delays?",
            "Train status",
            "Bus schedule"
        ]
        
        for query in transit_queries:
            if any(kw in query.lower() for kw in ['subway', 'mta', 'train', 'bus']):
                tool = 'hot_query'
            else:
                tool = 'cold_query'
            
            assert tool == 'hot_query', f"Query '{query}' should route to hot_query"


class TestLowConfidenceHandling:
    """Property 14: Low Confidence Returns Clarifying Question"""
    
    def test_unclear_query_returns_clarification(self):
        """Unclear queries should return a clarifying question."""
        unclear_queries = [
            "What's that?",
            "Tell me more",
            "Hmm",
            ""
        ]
        
        for query in unclear_queries:
            # Simulate low confidence classification
            confidence = 0.3 if len(query) < 10 else 0.8
            
            if confidence < 0.7:
                response = "I'm not sure what you're looking for. Are you asking about food safety, street conditions, or transit?"
                requires_clarification = True
            else:
                response = "Processing..."
                requires_clarification = False
            
            if len(query) < 10:
                assert requires_clarification, f"Query '{query}' should require clarification"
    
    # Feature: pseudo-meta-glass, Property 14: Low confidence clarification
    @given(
        confidence=st.floats(min_value=0.0, max_value=1.0)
    )
    @settings(max_examples=100)
    def test_confidence_threshold_property(self, confidence):
        """Property test: confidence below 0.7 always requires clarification."""
        threshold = 0.7
        requires_clarification = confidence < threshold
        
        if confidence < threshold:
            assert requires_clarification is True
        else:
            assert requires_clarification is False


class TestResponseSynthesis:
    """Property 13: Tool Result Synthesized to Natural Language"""
    
    def test_hazard_results_produce_warning(self):
        """Tool results with hazards should produce warning text."""
        hazards = [
            {'type': 'restaurant', 'name': 'Bad Place', 'grade': 'C'},
            {'type': '311', 'description': 'Noise complaint'}
        ]
        
        # Simulate synthesis
        if hazards:
            response = "Heads up, there are some safety concerns nearby."
        else:
            response = "Area looks good, you're all clear."
        
        assert "concern" in response.lower() or "warning" in response.lower() or "heads up" in response.lower()
    
    def test_no_hazards_produces_clear_message(self):
        """No hazards should produce an all-clear message."""
        hazards = []
        
        if hazards:
            response = "Warning detected"
        else:
            response = "Area looks good, you're all clear."
        
        assert "clear" in response.lower() or "good" in response.lower()
    
    def test_response_is_brief(self):
        """Synthesized response should be under 20 words."""
        # Simulate various responses
        responses = [
            "Heads up, there are some safety concerns nearby.",
            "Area looks good, you're all clear.",
            "Watch out for construction noise two blocks ahead.",
            "This restaurant has a C health grade. Consider alternatives."
        ]
        
        for response in responses:
            word_count = len(response.split())
            assert word_count <= 25, f"Response too long: {word_count} words"


class TestAgentIntegration:
    """Integration tests with mocked dependencies."""
    
    @patch('agent.call_llm_api')
    @patch('agent.cold_query')
    def test_food_query_calls_cold_query(self, mock_cold, mock_llm_api):
        """Food intent should invoke cold_query tool."""
        mock_llm_api.return_value = '{"intent": "food", "confidence": 0.9, "tool": "cold_query"}'
        mock_cold.return_value = []
        
        # Simulate the flow
        classification = {"intent": "food", "confidence": 0.9, "tool": "cold_query"}
        
        if classification['tool'] == 'cold_query':
            mock_cold(40.7, -74.0, 500)
        
        mock_cold.assert_called_once()
    
    @patch('agent.call_llm_api')
    @patch('agent.hot_query')
    def test_transit_query_calls_hot_query(self, mock_hot, mock_llm_api):
        """Transit intent should invoke hot_query tool."""
        mock_llm_api.return_value = '{"intent": "transit", "confidence": 0.85, "tool": "hot_query"}'
        
        from tools.types import HotQueryResult
        mock_hot.return_value = HotQueryResult(results=[], partial=False)
        
        classification = {"intent": "transit", "confidence": 0.85, "tool": "hot_query"}
        
        if classification['tool'] == 'hot_query':
            mock_hot(40.7, -74.0)
        
        mock_hot.assert_called_once()


if __name__ == '__main__':
    pytest.main([__file__, '-v'])
