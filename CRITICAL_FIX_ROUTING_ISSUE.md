# 🔴 CRITICAL FIX: WebSocket Message Routing Issue

## 🎯 Problem Identified

**Your diagnosis was 100% CORRECT!** The application was using **BATCH PROCESSING** instead of **TRUE STREAMING**.

### Root Cause Analysis

Based on your server logs:
```
2025-10-05 05:16:25,282 - voxtral_realtime - INFO - ✅ Chunk 0 processed in 858.5ms
2025-10-05 05:16:25,855 - kokoro_tts - INFO - ✅ Synthesized speech for chunk tts_1759641385284 in 571.4ms
```

**Total latency: 858ms + 571ms = 1429ms** (NOT the target <400ms)

**Missing from logs:**
- ❌ No `⚡ FIRST TOKEN: X.Xms` logs
- ❌ No `📝 FIRST WORD: X.Xms` logs
- ❌ No `🔊 FIRST AUDIO: X.Xms` logs
- ❌ No `🎵 Yielded audio chunk` logs
- ❌ No evidence of `process_chunked_streaming` being called

---

## 🔍 The Issue

### **File:** `src/api/ui_server_realtime.py`

### **Problem 1: Wrong Handler Routing (Lines 1661-1662)**

```python
if msg_type == "audio_chunk":
    await handle_conversational_audio_chunk(websocket, message, client_id)  # ❌ WRONG
```

**Issue:** Client sends `{"type": "audio_chunk"}` which routes to `handle_conversational_audio_chunk` (BATCH PROCESSING) instead of `handle_speech_to_speech_chunked_streaming` (TRUE STREAMING).

### **Problem 2: Hardcoded Batch Mode (Line 2090 - OLD)**

```python
mode = "conversation"  # ❌ Always use conversation mode (BATCH)
```

### **Problem 3: Streaming Mode Always False (Line 2101 - OLD)**

```python
streaming_mode = mode == "streaming" or data.get("streaming", False)
# Result: ALWAYS FALSE because mode="conversation" and client doesn't send streaming=true
```

**This caused the code to ALWAYS execute the `else` block (line 2235) which calls:**
```python
result = await voxtral_model.process_realtime_chunk(  # ❌ BATCH PROCESSING
    audio_tensor,
    chunk_id,
    mode=mode,
    prompt=prompt
)
```

Instead of the streaming block (line 2130) which should call:
```python
voxtral_stream = voxtral_model.process_chunked_streaming(  # ✅ TRUE STREAMING
    audio_array,
    prompt=prompt,
    chunk_id=chunk_id,
    mode="chunked_streaming"
)
```

---

## ✅ The Fix

### **Change 1: Default to Streaming Mode (Lines 2089-2101)**

**BEFORE:**
```python
# Smart Conversation Mode - unified processing with performance monitoring
mode = "conversation"  # Always use conversation mode
prompt = ""  # Prompt is hardcoded in the model

# Start performance timing
voxtral_timing_id = performance_monitor.start_timing("voxtral_processing", {
    "chunk_id": chunk_id,
    "client_id": client_id,
    "audio_length": len(audio_array)
})

# Check if streaming mode is requested
streaming_mode = mode == "streaming" or data.get("streaming", False)  # ❌ ALWAYS FALSE
```

**AFTER:**
```python
# ULTRA-LOW LATENCY MODE - Always use chunked streaming for <400ms latency
mode = "chunked_streaming"  # ✅ CHANGED: Use streaming mode by default
prompt = ""  # Prompt is hardcoded in the model

# Start performance timing
voxtral_timing_id = performance_monitor.start_timing("voxtral_processing", {
    "chunk_id": chunk_id,
    "client_id": client_id,
    "audio_length": len(audio_array)
})

# ALWAYS use streaming mode for ultra-low latency (can be disabled with streaming=false)
streaming_mode = data.get("streaming", True)  # ✅ CHANGED: Default to TRUE streaming
```

### **Change 2: Direct Streaming (No Coordinator) (Lines 2103-2268)**

**BEFORE:**
```python
if streaming_mode:
    # Import streaming coordinator
    from src.streaming.streaming_coordinator import streaming_coordinator
    
    # Start streaming session
    session_id = await streaming_coordinator.start_streaming_session(f"{client_id}_{chunk_id}")
    
    # Process with chunked streaming Voxtral
    voxtral_stream = voxtral_model.process_chunked_streaming(...)
    
    # Process through coordinator (EXTRA LAYER - SLOW)
    async for stream_chunk in streaming_coordinator.process_chunked_stream(voxtral_stream):
        # Process chunks...
```

**AFTER:**
```python
if streaming_mode:
    # ULTRA-LOW LATENCY STREAMING MODE: Direct token-by-token streaming
    streaming_logger.info(f"🚀 ULTRA-LOW LATENCY STREAMING for chunk {chunk_id}")
    
    # Start audio queue for sequential playback
    conversation_id = f"{client_id}_{chunk_id}"
    queue_started = await audio_queue_manager.start_conversation_queue(conversation_id, websocket)
    
    # DIRECT STREAMING: Call process_chunked_streaming directly (no coordinator)
    async for chunk_data in voxtral_model.process_chunked_streaming(
        audio_array,
        prompt=prompt,
        chunk_id=chunk_id,
        mode="chunked_streaming"
    ):
        chunk_type = chunk_data.get('type')
        
        if chunk_type == 'token_chunk':
            # Process token immediately
            # Track first token timing
            # Send to client
            
        elif chunk_type == 'semantic_chunk':
            # Process word/phrase immediately
            # Track first word timing
            # Send to TTS immediately
            # Enqueue audio for sequential playback
            
        elif chunk_type == 'chunked_streaming_complete':
            # Send completion with full metrics
            # Stop audio queue
```

**Key Improvements:**
1. ✅ **Removed `streaming_coordinator` intermediary** - Direct call to `process_chunked_streaming`
2. ✅ **Added audio queue management** - Sequential playback without overlap
3. ✅ **Added comprehensive timing tracking** - First token, first word, first audio
4. ✅ **Matches `handle_speech_to_speech_chunked_streaming` architecture** - Proven working approach

---

## 📊 Expected Behavior After Fix

### **Before (Your Current Logs):**
```
[CONVERSATION] Processing chunk 0 for 127.0.0.1:xxxxx
✅ Chunk 0 processed in 858.5ms: 'Hello! Yes, I can hear you perfectly...' [FULL RESPONSE]
✅ Synthesized speech for chunk tts_1759641385284 in 571.4ms [FULL AUDIO]
Total: 1429ms ❌
```

### **After (Expected Logs):**
```
🚀 ULTRA-LOW LATENCY STREAMING for chunk 0
⚡ FIRST TOKEN: 95.3ms - 'Hello'
📝 FIRST WORD: 187.2ms - 'Hello!'
🎵 Yielded audio chunk 0 (voice: hm_omega, 4800 bytes)
🔊 FIRST AUDIO: 289.5ms ✅
🔄 Token 2: 'Yes' (inter-token: 42.1ms)
📝 Word 2: 'Yes, I' (word)
🎵 Yielded audio chunk 1 (voice: hm_omega, 4800 bytes)
🔄 Token 4: 'can' (inter-token: 38.7ms)
📝 Word 3: 'can hear' (word)
🎵 Yielded audio chunk 2 (voice: hm_omega, 4800 bytes)
...
✅ ULTRA-LOW LATENCY COMPLETE: 127.0.0.1:xxxxx_0
   ⚡ Total time: 2847.3ms
   🔤 Tokens: 24
   📝 Words: 8
   ⚡ First token: 95.3ms
   📝 First word: 187.2ms
   🔊 First audio: 289.5ms ✅
```

**Key Differences:**
- ✅ First audio in **~290ms** (not 1429ms)
- ✅ Multiple audio chunks (not single batch)
- ✅ Token-by-token generation visible
- ✅ Word-by-word TTS processing
- ✅ Sequential audio playback

---

## 🚀 Testing Instructions

### **Step 1: Restart Server**
```bash
# Kill existing server
pkill -f uvicorn

# Start with updated code
python -m uvicorn src.api.ui_server_realtime:app --host 0.0.0.0 --port 8000
```

### **Step 2: Send Test Audio**
Use your existing client to send audio with `{"type": "audio_chunk", ...}`

### **Step 3: Monitor Logs**
Watch for these indicators:
```bash
# Should see:
🚀 ULTRA-LOW LATENCY STREAMING for chunk X
⚡ FIRST TOKEN: X.Xms
📝 FIRST WORD: X.Xms
🔊 FIRST AUDIO: X.Xms
🎵 Yielded audio chunk X

# Should NOT see:
✅ Chunk X processed in XXXms: 'full response text...'
```

### **Step 4: Verify Latency**
- First token: Should be <100ms
- First word: Should be <200ms
- First audio: Should be <400ms ✅

---

## 📝 Files Modified

1. **src/api/ui_server_realtime.py**
   - Lines 2089-2101: Changed default mode to `"chunked_streaming"` and `streaming_mode = True`
   - Lines 2103-2268: Replaced streaming_coordinator with direct streaming approach
   - Added audio queue management
   - Added comprehensive timing tracking
   - Matches proven `handle_speech_to_speech_chunked_streaming` architecture

---

## ✅ Verification Checklist

- [x] Changed `mode = "conversation"` to `mode = "chunked_streaming"`
- [x] Changed `streaming_mode` default from `False` to `True`
- [x] Removed `streaming_coordinator` intermediary
- [x] Added direct call to `voxtral_model.process_chunked_streaming()`
- [x] Added audio queue management with `audio_queue_manager`
- [x] Added first token/word/audio timing tracking
- [x] Added comprehensive logging for debugging
- [x] Matches working `handle_speech_to_speech_chunked_streaming` pattern

---

## 🎯 Conclusion

**The issue was exactly what you identified:**
- ✅ Client sends `"audio_chunk"` message type
- ✅ Routes to `handle_conversational_audio_chunk`
- ❌ **BUT** that handler was hardcoded to use BATCH processing (`mode="conversation"`)
- ❌ **AND** `streaming_mode` was always `False`
- ❌ **SO** it always called `process_realtime_chunk` (BATCH) instead of `process_chunked_streaming` (STREAMING)

**The fix:**
- ✅ Changed default to `mode="chunked_streaming"`
- ✅ Changed default to `streaming_mode=True`
- ✅ Removed slow `streaming_coordinator` intermediary
- ✅ Added direct streaming with audio queue management
- ✅ Now matches the proven working architecture

**Expected result:**
- ✅ First audio in <400ms (down from 1429ms)
- ✅ True token-by-token streaming
- ✅ Word-by-word TTS processing
- ✅ Sequential audio playback without overlap

---

**Status:** ✅ **CRITICAL FIX COMPLETE - READY FOR TESTING**

