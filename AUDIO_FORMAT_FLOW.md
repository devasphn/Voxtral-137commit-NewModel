# Audio Format Flow: Before vs After

## 🔴 BEFORE (Broken - DEMUXER_ERROR)

```
┌─────────────────────────────────────────────────────────────────┐
│ Kokoro TTS Model (synthesize_speech_streaming)                 │
│                                                                 │
│ audio_np = audio.cpu().numpy()                                  │
│ audio_bytes = (audio_np * 32767).astype(np.int16).tobytes()   │
│                                                                 │
│ Output: RAW PCM BYTES (int16 format)                          │
│ Example: [0x12, 0x34, 0x56, 0x78, ...]                        │
│ Size: 42,000 - 66,000 bytes                                    │
└─────────────────────────────────────────────────────────────────┘
                            ↓
┌─────────────────────────────────────────────────────────────────┐
│ Audio Queue Manager (_playback_worker)                         │
│                                                                 │
│ audio_b64 = base64.b64encode(audio_chunk.audio_data)          │
│                                                                 │
│ Output: BASE64 ENCODED RAW PCM                                 │
│ Still no WAV headers! ❌                                        │
└─────────────────────────────────────────────────────────────────┘
                            ↓
┌─────────────────────────────────────────────────────────────────┐
│ WebSocket Message                                               │
│                                                                 │
│ {                                                               │
│   "type": "sequential_audio",                                   │
│   "audio_data": "EjRWeHo..." (base64 RAW PCM)                  │
│   "sample_rate": 24000                                          │
│ }                                                               │
└─────────────────────────────────────────────────────────────────┘
                            ↓
┌─────────────────────────────────────────────────────────────────┐
│ Client JavaScript (handleSequentialAudio)                       │
│                                                                 │
│ const bytes = Uint8Array.from(atob(audioData), ...)           │
│ const audioBlob = new Blob([bytes], {type: 'audio/wav'})      │
│                                                                 │
│ Problem: Blob contains RAW PCM, not WAV! ❌                     │
│ Missing: RIFF header, fmt chunk, data chunk header             │
└─────────────────────────────────────────────────────────────────┘
                            ↓
┌─────────────────────────────────────────────────────────────────┐
│ Browser Audio Element                                           │
│                                                                 │
│ audio.src = URL.createObjectURL(audioBlob)                     │
│ audio.play()                                                    │
│                                                                 │
│ Result: DEMUXER_ERROR_COULD_NOT_OPEN ❌                         │
│ Reason: Browser cannot decode RAW PCM without WAV headers      │
└─────────────────────────────────────────────────────────────────┘
```

---

## 🟢 AFTER (Fixed - Audio Plays Successfully)

```
┌─────────────────────────────────────────────────────────────────┐
│ Kokoro TTS Model (synthesize_speech_streaming)                 │
│                                                                 │
│ audio_np = audio.cpu().numpy()                                  │
│ audio_bytes = (audio_np * 32767).astype(np.int16).tobytes()   │
│                                                                 │
│ Output: RAW PCM BYTES (int16 format)                          │
│ Example: [0x12, 0x34, 0x56, 0x78, ...]                        │
│ Size: 42,000 - 66,000 bytes                                    │
└─────────────────────────────────────────────────────────────────┘
                            ↓
┌─────────────────────────────────────────────────────────────────┐
│ Audio Queue Manager (_playback_worker)                         │
│                                                                 │
│ ✅ NEW: Convert PCM to WAV with proper headers                 │
│                                                                 │
│ wav_data = pcm_to_wav(                                         │
│     audio_chunk.audio_data,                                     │
│     sample_rate=24000,                                          │
│     num_channels=1,                                             │
│     bits_per_sample=16                                          │
│ )                                                               │
│                                                                 │
│ WAV Structure:                                                  │
│ ┌──────────────────────────────────────────┐                   │
│ │ RIFF Header (12 bytes)                   │                   │
│ │ - 'RIFF' (4 bytes)                       │                   │
│ │ - File size (4 bytes)                    │                   │
│ │ - 'WAVE' (4 bytes)                       │                   │
│ ├──────────────────────────────────────────┤                   │
│ │ fmt Chunk (24 bytes)                     │                   │
│ │ - 'fmt ' (4 bytes)                       │                   │
│ │ - Chunk size: 16 (4 bytes)               │                   │
│ │ - Audio format: 1/PCM (2 bytes)          │                   │
│ │ - Channels: 1 (2 bytes)                  │                   │
│ │ - Sample rate: 24000 (4 bytes)           │                   │
│ │ - Byte rate (4 bytes)                    │                   │
│ │ - Block align (2 bytes)                  │                   │
│ │ - Bits per sample: 16 (2 bytes)          │                   │
│ ├──────────────────────────────────────────┤                   │
│ │ data Chunk Header (8 bytes)              │                   │
│ │ - 'data' (4 bytes)                       │                   │
│ │ - Data size (4 bytes)                    │                   │
│ ├──────────────────────────────────────────┤                   │
│ │ PCM Audio Data (42,000-66,000 bytes)     │                   │
│ │ [0x12, 0x34, 0x56, 0x78, ...]            │                   │
│ └──────────────────────────────────────────┘                   │
│                                                                 │
│ Total Size: 44 + PCM size = 42,044 - 66,044 bytes ✅           │
│                                                                 │
│ audio_b64 = base64.b64encode(wav_data)                         │
└─────────────────────────────────────────────────────────────────┘
                            ↓
┌─────────────────────────────────────────────────────────────────┐
│ WebSocket Message                                               │
│                                                                 │
│ {                                                               │
│   "type": "sequential_audio",                                   │
│   "audio_data": "UklGRi4..." (base64 COMPLETE WAV FILE) ✅     │
│   "sample_rate": 24000,                                         │
│   "format": "wav",                                              │
│   "chunk_size_bytes": 42044                                     │
│ }                                                               │
└─────────────────────────────────────────────────────────────────┘
                            ↓
┌─────────────────────────────────────────────────────────────────┐
│ Client JavaScript (handleSequentialAudio)                       │
│                                                                 │
│ const bytes = Uint8Array.from(atob(audioData), ...)           │
│                                                                 │
│ ✅ NEW: Validate WAV format                                     │
│ const riffCheck = String.fromCharCode(bytes[0..3])            │
│ const waveCheck = String.fromCharCode(bytes[8..11])           │
│                                                                 │
│ if (riffCheck === 'RIFF' && waveCheck === 'WAVE') {           │
│   log('✅ Valid WAV file detected')                            │
│   const audioBlob = new Blob([bytes], {type: 'audio/wav'})    │
│ }                                                               │
│                                                                 │
│ Result: Blob contains COMPLETE WAV FILE ✅                      │
└─────────────────────────────────────────────────────────────────┘
                            ↓
┌─────────────────────────────────────────────────────────────────┐
│ Browser Audio Element                                           │
│                                                                 │
│ audio.src = URL.createObjectURL(audioBlob)                     │
│ audio.play()                                                    │
│                                                                 │
│ Result: ✅ AUDIO PLAYS SUCCESSFULLY!                            │
│ - Browser decodes WAV headers                                   │
│ - Extracts PCM data                                             │
│ - Plays audio at 24kHz sample rate                             │
│ - Duration: ~2.5 seconds per chunk                             │
└─────────────────────────────────────────────────────────────────┘
```

---

## 📊 WAV File Structure Details

### **WAV Header Breakdown (44 bytes total):**

```
Offset  Size  Field                Value (Example)
------  ----  -------------------  ---------------
0       4     ChunkID              'RIFF' (0x52494646)
4       4     ChunkSize            42000 + 36 = 42036
8       4     Format               'WAVE' (0x57415645)

12      4     Subchunk1ID          'fmt ' (0x666d7420)
16      4     Subchunk1Size        16 (for PCM)
20      2     AudioFormat          1 (PCM)
22      2     NumChannels          1 (mono)
24      4     SampleRate           24000
28      4     ByteRate             48000 (24000 * 1 * 16/8)
32      2     BlockAlign           2 (1 * 16/8)
34      2     BitsPerSample        16

36      4     Subchunk2ID          'data' (0x64617461)
40      4     Subchunk2Size        42000 (PCM data size)

44      *     Data                 [PCM samples...]
```

### **Hex Dump Example:**

```
Offset(h) 00 01 02 03 04 05 06 07 08 09 0A 0B 0C 0D 0E 0F

00000000  52 49 46 46 2E A4 00 00 57 41 56 45 66 6D 74 20  RIFF....WAVEfmt 
00000010  10 00 00 00 01 00 01 00 C0 5D 00 00 80 BB 00 00  .........].......
00000020  02 00 10 00 64 61 74 61 A0 A4 00 00 12 34 56 78  ....data.....4Vx
00000030  [PCM audio data continues...]
```

**Explanation:**
- `52 49 46 46` = 'RIFF'
- `2E A4 00 00` = File size (42030 bytes in little-endian)
- `57 41 56 45` = 'WAVE'
- `66 6D 74 20` = 'fmt '
- `10 00 00 00` = fmt chunk size (16)
- `01 00` = Audio format (1 = PCM)
- `01 00` = Channels (1 = mono)
- `C0 5D 00 00` = Sample rate (24000 in little-endian)
- `80 BB 00 00` = Byte rate (48000 in little-endian)
- `02 00` = Block align (2)
- `10 00` = Bits per sample (16)
- `64 61 74 61` = 'data'
- `A0 A4 00 00` = Data size (42000 bytes in little-endian)
- `12 34 56 78...` = PCM audio samples

---

## 🎯 Key Differences

| Aspect | BEFORE ❌ | AFTER ✅ |
|--------|----------|---------|
| **Format** | Raw PCM bytes | Complete WAV file |
| **Headers** | None | 44-byte WAV header |
| **Size** | 42,000-66,000 bytes | 42,044-66,044 bytes (+44) |
| **Browser** | Cannot decode | Decodes successfully |
| **Playback** | DEMUXER_ERROR | Plays perfectly |
| **Validation** | None | RIFF/WAVE check |

---

## ✅ Conclusion

The fix adds proper WAV headers to raw PCM audio data, allowing browsers to decode and play the audio chunks successfully. The 44-byte overhead is negligible compared to the audio data size, and the browser can now properly interpret the audio format, sample rate, and channel configuration.

**Result:** Audio playback works perfectly! 🎉

