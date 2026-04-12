/**
 * CooldownCache Unit Tests
 * 
 * Property 19: Cooldown Cache Round-Trip
 */

import { jest } from '@jest/globals';
import fc from 'fast-check';

// Mock timers for TTL testing
jest.useFakeTimers();

// Import after mocking
import cooldownCache from '../cooldownCache.js';

describe('CooldownCache', () => {
  beforeEach(() => {
    cooldownCache.clearAll();
    jest.setSystemTime(new Date('2026-04-11T12:00:00Z'));
  });

  afterEach(() => {
    jest.useRealTimers();
    jest.useFakeTimers();
  });

  test('fresh ID is not suppressed', () => {
    expect(cooldownCache.isSuppressed('new_id')).toBe(false);
  });

  test('ID is suppressed after record()', () => {
    cooldownCache.record('test_id');
    expect(cooldownCache.isSuppressed('test_id')).toBe(true);
  });

  test('ID is not suppressed after 30 minutes', () => {
    cooldownCache.record('test_id');
    expect(cooldownCache.isSuppressed('test_id')).toBe(true);
    
    // Advance time by 31 minutes
    jest.advanceTimersByTime(31 * 60 * 1000);
    
    expect(cooldownCache.isSuppressed('test_id')).toBe(false);
  });

  test('ID is still suppressed at 29 minutes', () => {
    cooldownCache.record('test_id');
    
    // Advance time by 29 minutes
    jest.advanceTimersByTime(29 * 60 * 1000);
    
    expect(cooldownCache.isSuppressed('test_id')).toBe(true);
  });

  test('different IDs are independent', () => {
    cooldownCache.record('id_1');
    
    expect(cooldownCache.isSuppressed('id_1')).toBe(true);
    expect(cooldownCache.isSuppressed('id_2')).toBe(false);
  });

  test('clear() removes specific ID', () => {
    cooldownCache.record('id_1');
    cooldownCache.record('id_2');
    
    cooldownCache.clear('id_1');
    
    expect(cooldownCache.isSuppressed('id_1')).toBe(false);
    expect(cooldownCache.isSuppressed('id_2')).toBe(true);
  });

  test('clearAll() removes all IDs', () => {
    cooldownCache.record('id_1');
    cooldownCache.record('id_2');
    cooldownCache.record('id_3');
    
    cooldownCache.clearAll();
    
    expect(cooldownCache.size()).toBe(0);
    expect(cooldownCache.isSuppressed('id_1')).toBe(false);
    expect(cooldownCache.isSuppressed('id_2')).toBe(false);
  });

  test('size() returns correct count', () => {
    expect(cooldownCache.size()).toBe(0);
    
    cooldownCache.record('id_1');
    expect(cooldownCache.size()).toBe(1);
    
    cooldownCache.record('id_2');
    expect(cooldownCache.size()).toBe(2);
    
    // Recording same ID doesn't increase size
    cooldownCache.record('id_1');
    expect(cooldownCache.size()).toBe(2);
  });

  test('getCooldownMs() returns 30 minutes', () => {
    expect(cooldownCache.getCooldownMs()).toBe(30 * 60 * 1000);
  });

  // Feature: pseudo-meta-glass, Property 19: Cooldown Cache Round-Trip
  test('Property 19: round-trip suppression within 30 minutes', () => {
    fc.assert(
      fc.property(fc.string({ minLength: 1, maxLength: 100 }), (id) => {
        cooldownCache.clearAll();
        jest.setSystemTime(new Date('2026-04-11T12:00:00Z'));
        
        // Initially not suppressed
        expect(cooldownCache.isSuppressed(id)).toBe(false);
        
        // Record and verify suppressed
        cooldownCache.record(id);
        expect(cooldownCache.isSuppressed(id)).toBe(true);
        
        // Still suppressed at 29 minutes
        jest.advanceTimersByTime(29 * 60 * 1000);
        expect(cooldownCache.isSuppressed(id)).toBe(true);
        
        // Not suppressed after 31 minutes total
        jest.advanceTimersByTime(2 * 60 * 1000);
        expect(cooldownCache.isSuppressed(id)).toBe(false);
      }),
      { numRuns: 100 }
    );
  });
});
