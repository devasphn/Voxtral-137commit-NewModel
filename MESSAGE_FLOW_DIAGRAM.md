# Message Flow Diagram - Before vs After

## 🔴 BEFORE (Broken - Missing Handlers)

```
Server                                  Client
------                                  ------

[User speaks]
    ↓
process_chunked_streaming()
    ↓
yield token_chunk                   →   ws.onmessage
    {                                       ↓
      type: "token_chunk",              handleWebSocketMessage()
      text: "Hello",                        ↓
      chunk_sequence: 1                 switch(data.type)
    }                                       ↓
                                        default:
                                            ❌ Unknown message type: token_chunk
                                            (Token ignored, not displayed)
    ↓
yield semantic_chunk                →   ws.onmessage
    {                                       ↓
      type: "semantic_chunk",           handleWebSocketMessage()
      text: "Hello!",                       ↓
      full_text_so_far: "Hello!"        switch(data.type)
    }                                       ↓
                                        default:
                                            ❌ Unknown message type: semantic_chunk
                                            (Word ignored, not displayed)
    ↓
synthesize_speech_streaming()
    ↓
yield audio_chunk                   →   ws.onmessage
    {                                       ↓
      type: "sequential_audio",         handleWebSocketMessage()
      audio_data: "UklGRi4...",             ↓
      sample_rate: 24000                switch(data.type)
    }                                       ↓
                                        case 'sequential_audio':
                                            handleSequentialAudio(data)
                                                ↓
                                            audioQueue.push({
                                                chunkId: data.chunk_id,
                                                audioData: data.audio_data,
                                                sampleRate: 24000,
                                                // ❌ No metadata object!
                                            })
                                                ↓
                                            playAudioItem(audioItem)
                                                ↓
                                            audio.addEventListener('canplaythrough', () => {
                                                // ❌ TypeError: Cannot read 'audio_duration_ms' of undefined
                                                log(`${metadata.audio_duration_ms}ms`);
                                            })
    ↓
yield chunked_streaming_complete    →   ws.onmessage
    {                                       ↓
      type: "chunked_streaming_complete", handleWebSocketMessage()
      response_text: "Hello! Yes...",       ↓
      total_tokens: 14,                 switch(data.type)
      first_token_latency_ms: 95.3          ↓
    }                                   default:
                                            ❌ Unknown message type: chunked_streaming_complete
                                            (Statistics ignored, not displayed)
                                            ❌ pendingResponse NOT reset!
                                                ↓
                                            [Second interaction blocked]
```

---

## 🟢 AFTER (Fixed - All Handlers Working)

```
Server                                  Client
------                                  ------

[User speaks]
    ↓
process_chunked_streaming()
    ↓
yield token_chunk                   →   ws.onmessage
    {                                       ↓
      type: "token_chunk",              handleWebSocketMessage()
      text: "Hello",                        ↓
      chunk_sequence: 1,                switch(data.type)
      inter_token_latency_ms: 95.3          ↓
    }                                   case 'token_chunk':
                                            ✅ handleTokenChunk(data)
                                                ↓
                                            log(`🔤 Token 1: "Hello" (95.3ms)`)
                                            responseDiv.textContent += "Hello"
                                            updateStatus('🔄 Streaming response...')
    ↓
yield token_chunk                   →   ws.onmessage
    {                                       ↓
      type: "token_chunk",              handleWebSocketMessage()
      text: "!",                            ↓
      chunk_sequence: 2,                switch(data.type)
      inter_token_latency_ms: 42.1          ↓
    }                                   case 'token_chunk':
                                            ✅ handleTokenChunk(data)
                                                ↓
                                            log(`🔤 Token 2: "!" (42.1ms)`)
                                            responseDiv.textContent += "!"
    ↓
yield semantic_chunk                →   ws.onmessage
    {                                       ↓
      type: "semantic_chunk",           handleWebSocketMessage()
      text: "Hello!",                       ↓
      full_text_so_far: "Hello!",       switch(data.type)
      chunk_sequence: 1,                    ↓
      boundary_type: "word"             case 'semantic_chunk':
    }                                       ✅ handleSemanticChunk(data)
                                                ↓
                                            log(`📝 Semantic chunk 1: "Hello!" (word)`)
                                            responseDiv.textContent = "Hello!"
                                            updateStatus('🔄 Streaming: "Hello!"...')
    ↓
synthesize_speech_streaming()
    ↓
yield audio_chunk                   →   ws.onmessage
    {                                       ↓
      type: "sequential_audio",         handleWebSocketMessage()
      audio_data: "UklGRi4...",             ↓
      sample_rate: 24000,               switch(data.type)
      chunk_size_bytes: 63644,              ↓
      text_source: "Hello!"             case 'sequential_audio':
    }                                       ✅ handleSequentialAudio(data)
                                                ↓
                                            // ✅ Calculate metadata
                                            const sampleRate = 24000;
                                            const audioDurationMs = (63644 - 44) / (24000 * 2) * 1000;
                                                ↓
                                            audioQueue.push({
                                                chunkId: data.chunk_id,
                                                audioData: data.audio_data,
                                                sampleRate: 24000,
                                                // ✅ Metadata object included!
                                                metadata: {
                                                    audio_duration_ms: 2650,
                                                    sample_rate: 24000,
                                                    chunk_size_bytes: 63644,
                                                    format: 'wav'
                                                }
                                            })
                                                ↓
                                            playAudioItem(audioItem)
                                                ↓
                                            // ✅ Extract metadata and sampleRate
                                            const { metadata = {}, sampleRate = 24000 } = audioItem;
                                                ↓
                                            audio.addEventListener('loadedmetadata', () => {
                                                // ✅ Use sampleRate from audioItem
                                                log(`Sample Rate: ${sampleRate}Hz`);
                                                // Output: "Sample Rate: 24000Hz" ✅
                                            })
                                                ↓
                                            audio.addEventListener('canplaythrough', () => {
                                                // ✅ Safe access with optional chaining
                                                const durationMs = metadata?.audio_duration_ms || 'unknown';
                                                log(`${durationMs}ms`);
                                                // Output: "2650ms" ✅
                                            })
                                                ↓
                                            [Audio plays successfully] ✅
    ↓
yield chunked_streaming_complete    →   ws.onmessage
    {                                       ↓
      type: "chunked_streaming_complete", handleWebSocketMessage()
      response_text: "Hello! Yes...",       ↓
      total_tokens: 14,                 switch(data.type)
      total_words: 9,                       ↓
      total_time_ms: 31427.6,           case 'chunked_streaming_complete':
      first_token_latency_ms: 95.3,         ✅ handleStreamingComplete(data)
      first_word_latency_ms: 187.2,             ↓
      first_audio_latency_ms: 289.5         log(`✅ Streaming complete: "Hello! Yes..."`)
    }                                       log(`   📊 Stats: 14 tokens, 9 words in 31427.6ms`)
                                            log(`   ⚡ First token: 95.3ms`)
                                            log(`   📝 First word: 187.2ms`)
                                            log(`   🔊 First audio: 289.5ms`)
                                                ↓
                                            responseDiv.textContent = "Hello! Yes..."
                                            updateStatus('✅ Response complete (31428ms)')
                                                ↓
                                            // ✅ CRITICAL: Reset flag for next interaction
                                            pendingResponse = false;
                                                ↓
                                            [Second interaction ready] ✅
```

---

## 📊 Message Type Comparison

| Message Type | Before | After |
|--------------|--------|-------|
| `token_chunk` | ❌ Unknown message type | ✅ Handled by `handleTokenChunk()` |
| `semantic_chunk` | ❌ Unknown message type | ✅ Handled by `handleSemanticChunk()` |
| `sequential_audio` | ⚠️ Handled but metadata missing | ✅ Handled with complete metadata |
| `chunked_streaming_complete` | ❌ Unknown message type | ✅ Handled by `handleStreamingComplete()` |

---

## 🔄 Second Interaction Flow

### **BEFORE (Blocked):**
```
First Interaction Complete
    ↓
pendingResponse = true (still set from first interaction) ❌
    ↓
[User speaks again]
    ↓
audioWorkletNode.onaudioprocess = (event) => {
    if (!isStreaming || pendingResponse) return;  // ❌ Returns early!
    // Audio processing blocked
}
    ↓
[No response generated] ❌
```

### **AFTER (Working):**
```
First Interaction Complete
    ↓
handleStreamingComplete(data)
    ↓
pendingResponse = false;  // ✅ Reset!
    ↓
[User speaks again]
    ↓
audioWorkletNode.onaudioprocess = (event) => {
    if (!isStreaming || pendingResponse) return;  // ✅ Continues!
    // Audio processing continues
    sendAudioChunk(audioData);
}
    ↓
[Response generated successfully] ✅
```

---

## 🎯 Key Improvements

### **1. Real-Time Token Display**
```
Before: Tokens ignored ❌
After:  🔤 Token 1: "Hello" (95.3ms) ✅
        🔤 Token 2: "!" (42.1ms) ✅
```

### **2. Real-Time Word Display**
```
Before: Words ignored ❌
After:  📝 Semantic chunk 1: "Hello!" (word) ✅
        📝 Semantic chunk 2: "Yes, I" (word) ✅
```

### **3. Proper Audio Metadata**
```
Before: TypeError: Cannot read 'audio_duration_ms' of undefined ❌
        Sample Rate: unknownHz ❌
        
After:  Sample Rate: 24000Hz ✅
        Audio chunk ready to play (2650ms) ✅
```

### **4. Completion Statistics**
```
Before: Statistics ignored ❌

After:  ✅ Streaming complete: "Hello! Yes, I can hear you..."
           📊 Stats: 14 tokens, 9 words in 31427.6ms
           ⚡ First token: 95.3ms
           📝 First word: 187.2ms
           🔊 First audio: 289.5ms ✅
```

### **5. Multiple Interactions**
```
Before: Second interaction blocked ❌

After:  First interaction → Complete → pendingResponse = false
        Second interaction → Works perfectly ✅
        Third interaction → Works perfectly ✅
```

---

## ✅ Conclusion

**All message types are now properly handled:**
- ✅ `token_chunk` → Real-time token display
- ✅ `semantic_chunk` → Real-time word display
- ✅ `sequential_audio` → Audio playback with complete metadata
- ✅ `chunked_streaming_complete` → Statistics and flag reset

**All errors fixed:**
- ✅ No more TypeError
- ✅ No more "Unknown message type" warnings
- ✅ Sample rate displays correctly
- ✅ Multiple interactions work perfectly

**Result:** Complete ultra-low latency streaming with real-time feedback! 🎉

