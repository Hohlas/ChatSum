# Plan: Fix for Raw JSON in First Part of Summary

## Problem Analysis

The issue is that Part 1 of the summary contains raw JSON messages instead of a proper AI-generated summary. 

### Root Cause

The regex pattern in [`split_summary_into_parts()`](main.py:973) function is incorrect:

```python
pattern = r'═+\nЧАСТЬ (\d+) \(сообщения (\d+)-(\d+)\):.*?\n═+\n(.*?)(?=\n═+\nЧАСТЬ |\Z)'
```

The combined summary is formatted as:
```
═══════════════════════════════════════
ЧАСТЬ 1 (сообщения 1-500):
═══════════════════════════════════════
<content>
═══════════════════════════════════════
```

The regex expects `:\n═+\n` (colon, newline, separators, newline), but the actual format has `:\n════════` (colon, newline, then directly more equal signs without a trailing newline after the header separator).

When the regex fails to match, it falls back to returning the entire text as-is (`line 978-979`):
```python
if not matches:
    # Не удалось распарсить - возвращаем как есть
    return [(None, summary_text, None, None)]
```

This causes the raw combined summary (with all parts concatenated) to be treated as a single part, and when Telegram displays the first part, it shows the beginning of this raw combined text - which is the raw JSON header from the AI response.

## Fix Plan

### Step 1: Fix the regex pattern in `split_summary_into_parts()`

Change the regex to correctly match the actual format:
- Match `══\n` after the header line (not `══\n` after the header separator)

### Step 2: Add logging for debugging

Add a check to log when regex fails to parse, helping with future debugging.

### Step 3: Consider adding a fallback format check

If the primary format fails, try alternative formats.

## Implementation Details

**File**: `main.py`  
**Function**: `split_summary_into_parts()` (line 952-990)

**Current regex (broken)**:
```python
pattern = r'═+\nЧАСТЬ (\d+) \(сообщения (\d+)-(\d+)\):.*?\n═+\n(.*?)(?=\n═+\nЧАСТЬ |\Z)'
```

**Suggested fix**:
```python
# Match format: ═══\nЧАСТЬ N (сообщения X-Y):\n═══\n<content>
pattern = r'═+\nЧАСТЬ (\d+) \(сообщения (\d+)-(\d+)\):\n═+\n(.*?)(?=\n═+\nЧАСТЬ |\Z)'
```

The key difference: removed `.*?` before the second `═+\n` and adjusted to match `:\n═+\n` directly.
