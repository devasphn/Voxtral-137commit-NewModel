#!/usr/bin/env python3
"""
Test script for word-by-word streaming generation using TextIteratorStreamer
Verifies ultra-low latency performance with <500ms first word target
"""

import asyncio
import time
import numpy as np
import tempfile
import soundfile as sf
import json
import websockets
import base64
from typing import List, Dict

async def test_direct_streaming():
    """Test direct Voxtral streaming without WebSocket"""
    print("🧪 Testing Direct Voxtral Word-by-Word Streaming")
    print("=" * 60)
    
    try:
        # Import the model
        from src.models.voxtral_model_realtime import voxtral_model
        
        # Ensure model is initialized
        if not voxtral_model.is_initialized:
            print("🔄 Initializing Voxtral model...")
            await voxtral_model.initialize()
            print("✅ Model initialized")
        
        # Generate realistic test audio (speech-like signal)
        sample_rate = 16000
        duration = 2.0
        t = np.linspace(0, duration, int(sample_rate * duration))
        
        # Multi-formant speech-like signal
        audio = (
            0.4 * np.sin(2 * np.pi * 120 * t) +  # F0 (fundamental)
            0.3 * np.sin(2 * np.pi * 400 * t) +  # F1 (first formant)
            0.2 * np.sin(2 * np.pi * 900 * t) +  # F2 (second formant)
            0.1 * np.sin(2 * np.pi * 1300 * t)   # F3 (third formant)
        )
        
        # Add realistic envelope and noise
        envelope = np.exp(-0.3 * t) * (1 + 0.3 * np.sin(2 * np.pi * 3 * t))
        audio = audio * envelope + np.random.normal(0, 0.005, len(audio))
        audio = audio.astype(np.float32) * 0.4
        
        print(f"🎙️ Generated test audio: {len(audio)} samples ({duration}s)")
        
        # Test streaming generation
        start_time = time.time()
        first_token_time = None
        first_word_time = None
        tokens_received = []
        words_received = []
        
        print("\n🚀 Starting word-by-word streaming...")
        
        async for chunk_data in voxtral_model.process_chunked_streaming(
            audio,
            prompt=None,  # Audio-only mode
            chunk_id="word_streaming_test",
            mode="chunked_streaming"
        ):
            chunk_type = chunk_data.get('type')
            current_time = time.time()
            
            if chunk_type == 'token_chunk':
                if first_token_time is None:
                    first_token_time = current_time
                    first_token_latency = (first_token_time - start_time) * 1000
                    print(f"⚡ FIRST TOKEN: {first_token_latency:.1f}ms - '{chunk_data.get('text', '')}'")
                
                tokens_received.append({
                    'text': chunk_data.get('text', ''),
                    'time': current_time,
                    'latency': (current_time - start_time) * 1000
                })
                
                print(f"🔤 Token {len(tokens_received)}: '{chunk_data.get('text', '')}' "
                      f"({(current_time - start_time) * 1000:.1f}ms)")
            
            elif chunk_type == 'semantic_chunk':
                if first_word_time is None:
                    first_word_time = current_time
                    first_word_latency = (first_word_time - start_time) * 1000
                    print(f"🎯 FIRST WORD: {first_word_latency:.1f}ms - '{chunk_data.get('text', '')}'")
                
                words_received.append({
                    'text': chunk_data.get('text', ''),
                    'boundary_type': chunk_data.get('boundary_type', 'unknown'),
                    'time': current_time,
                    'latency': (current_time - start_time) * 1000
                })
                
                print(f"📝 Word {len(words_received)}: '{chunk_data.get('text', '')}' "
                      f"({chunk_data.get('boundary_type', 'unknown')}, "
                      f"{(current_time - start_time) * 1000:.1f}ms)")
            
            elif chunk_type == 'complete':
                total_time = (current_time - start_time) * 1000
                response_text = chunk_data.get('response_text', '')
                total_tokens = chunk_data.get('total_tokens', 0)
                first_token_latency_ms = chunk_data.get('first_token_latency_ms', 0)
                
                print(f"\n✅ STREAMING COMPLETE: {total_time:.1f}ms")
                print(f"📊 Response: '{response_text}'")
                print(f"🔢 Total tokens: {total_tokens}")
                print(f"📝 Total words: {len(words_received)}")
                print(f"⚡ First token latency: {first_token_latency_ms:.1f}ms")
                
                # Performance analysis
                print(f"\n📈 PERFORMANCE ANALYSIS:")
                print(f"   🎯 Target: <500ms first word")
                
                if first_word_time:
                    first_word_latency = (first_word_time - start_time) * 1000
                    print(f"   🚀 First word: {first_word_latency:.1f}ms")
                    print(f"   ✅ Target met: {'YES' if first_word_latency < 500 else 'NO'}")
                else:
                    print(f"   ❌ No words received")
                
                if first_token_time:
                    first_token_latency = (first_token_time - start_time) * 1000
                    print(f"   ⚡ First token: {first_token_latency:.1f}ms")
                
                return first_word_latency < 500 if first_word_time else False
            
            elif chunk_type == 'error':
                print(f"❌ Error: {chunk_data.get('error', 'Unknown error')}")
                return False
        
        print("❌ No completion received")
        return False
        
    except Exception as e:
        print(f"❌ Direct streaming test failed: {e}")
        import traceback
        traceback.print_exc()
        return False

async def test_websocket_streaming():
    """Test WebSocket streaming with word-by-word generation"""
    print("\n🌐 Testing WebSocket Word-by-Word Streaming")
    print("=" * 60)
    
    try:
        # Generate test audio
        sample_rate = 16000
        duration = 2.0
        t = np.linspace(0, duration, int(sample_rate * duration))
        
        audio = (
            0.4 * np.sin(2 * np.pi * 120 * t) +
            0.3 * np.sin(2 * np.pi * 400 * t) +
            0.2 * np.sin(2 * np.pi * 900 * t)
        )
        envelope = np.exp(-0.3 * t) * (1 + 0.3 * np.sin(2 * np.pi * 3 * t))
        audio = (audio * envelope + np.random.normal(0, 0.005, len(audio))).astype(np.float32) * 0.4
        
        # Save to temporary file for WebSocket transmission
        with tempfile.NamedTemporaryFile(suffix='.wav', delete=False) as tmp:
            sf.write(tmp.name, audio, sample_rate)
            with open(tmp.name, 'rb') as f:
                audio_bytes = f.read()
        
        print(f"🎙️ Generated test audio: {len(audio)} samples, {len(audio_bytes)} bytes")
        
        # Connect to WebSocket
        async with websockets.connect('ws://localhost:8000/ws') as websocket:
            print("🔗 Connected to WebSocket")
            
            # Send speech-to-speech request
            message = {
                'type': 'speech_to_speech',
                'audio_data': base64.b64encode(audio_bytes).decode('utf-8'),
                'conversation_id': 'word_streaming_test',
                'voice': 'hm_omega'
            }
            
            await websocket.send(json.dumps(message))
            print("📤 Sent audio data")
            
            start_time = time.time()
            first_token_time = None
            first_word_time = None
            first_audio_time = None
            tokens_received = []
            words_received = []
            audio_chunks = []
            
            # Listen for streaming responses
            timeout_count = 0
            while timeout_count < 30:  # 30 second timeout
                try:
                    response = await asyncio.wait_for(websocket.recv(), timeout=1.0)
                    data = json.loads(response)
                    
                    response_type = data.get('type')
                    current_time = time.time()
                    
                    if response_type == 'token_chunk':
                        if first_token_time is None:
                            first_token_time = current_time
                            first_token_latency = (first_token_time - start_time) * 1000
                            print(f"⚡ FIRST TOKEN: {first_token_latency:.1f}ms - '{data.get('text', '')}'")
                        
                        tokens_received.append(data.get('text', ''))
                        print(f"🔤 Token {len(tokens_received)}: '{data.get('text', '')}' "
                              f"({(current_time - start_time) * 1000:.1f}ms)")
                    
                    elif response_type == 'semantic_chunk':
                        if first_word_time is None:
                            first_word_time = current_time
                            first_word_latency = (first_word_time - start_time) * 1000
                            print(f"🎯 FIRST WORD: {first_word_latency:.1f}ms - '{data.get('text', '')}'")
                        
                        words_received.append(data.get('text', ''))
                        print(f"📝 Word {len(words_received)}: '{data.get('text', '')}' "
                              f"({(current_time - start_time) * 1000:.1f}ms)")
                    
                    elif response_type == 'chunked_streaming_audio':
                        if first_audio_time is None:
                            first_audio_time = current_time
                            audio_latency = (first_audio_time - start_time) * 1000
                            print(f"🎵 FIRST AUDIO: {audio_latency:.1f}ms")
                        
                        audio_size = len(base64.b64decode(data.get('audio_data', '')))
                        audio_chunks.append(audio_size)
                        print(f"🔊 Audio {len(audio_chunks)}: {audio_size} bytes")
                    
                    elif response_type == 'chunked_streaming_complete':
                        total_time = (current_time - start_time) * 1000
                        print(f"\n✅ WEBSOCKET STREAMING COMPLETE: {total_time:.1f}ms")
                        print(f"📊 Response: '{data.get('response_text', '')}'")
                        print(f"🔢 Total tokens: {data.get('total_tokens', 0)}")
                        print(f"📝 Total words: {len(words_received)}")
                        print(f"🔊 Audio chunks: {len(audio_chunks)}")
                        
                        if first_word_time:
                            first_word_latency = (first_word_time - start_time) * 1000
                            print(f"🎯 First word latency: {first_word_latency:.1f}ms")
                            print(f"✅ Target (<500ms): {'PASSED' if first_word_latency < 500 else 'FAILED'}")
                            return first_word_latency < 500
                        else:
                            print("❌ No words received")
                            return False
                    
                    elif response_type == 'error':
                        print(f"❌ Error: {data.get('message', 'Unknown error')}")
                        return False
                    
                    elif response_type == 'processing':
                        print(f"🔄 {data.get('message', '')}")
                    
                    timeout_count = 0  # Reset on successful message
                    
                except asyncio.TimeoutError:
                    timeout_count += 1
                    if timeout_count % 5 == 0:
                        print(f"⏳ Waiting... ({timeout_count}s)")
            
            print("❌ WebSocket timeout")
            return False
            
    except Exception as e:
        print(f"❌ WebSocket streaming test failed: {e}")
        import traceback
        traceback.print_exc()
        return False

async def main():
    """Run comprehensive word-by-word streaming tests"""
    print("🎯 WORD-BY-WORD STREAMING TEST SUITE")
    print("Target: <500ms first word latency")
    print("=" * 80)
    
    # Test 1: Direct streaming
    direct_result = await test_direct_streaming()
    
    # Test 2: WebSocket streaming
    websocket_result = await test_websocket_streaming()
    
    # Final results
    print("\n" + "=" * 80)
    print("🏁 FINAL RESULTS")
    print("=" * 80)
    print(f"📊 Direct streaming: {'✅ PASSED' if direct_result else '❌ FAILED'}")
    print(f"🌐 WebSocket streaming: {'✅ PASSED' if websocket_result else '❌ FAILED'}")
    
    overall_success = direct_result and websocket_result
    print(f"\n🎯 OVERALL: {'✅ SUCCESS - Word-by-word streaming working!' if overall_success else '❌ NEEDS MORE WORK'}")
    
    return overall_success

if __name__ == "__main__":
    result = asyncio.run(main())
    exit(0 if result else 1)
