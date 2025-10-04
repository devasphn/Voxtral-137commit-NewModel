"""
Test Script for Ultra-Low Latency Streaming Verification
Tests token-by-token generation, TTS streaming, and sequential audio playback
"""
import asyncio
import time
import json
import websockets
import base64
import numpy as np
import soundfile as sf
from pathlib import Path

# Test configuration
WS_URL = "ws://localhost:8000/ws"
TEST_AUDIO_FILE = "test_audio.wav"  # Create a test audio file
TARGET_FIRST_TOKEN_MS = 100
TARGET_FIRST_WORD_MS = 200
TARGET_FIRST_AUDIO_MS = 400
TARGET_INTER_TOKEN_MS = 50

class StreamingLatencyTester:
    """Test ultra-low latency streaming pipeline"""
    
    def __init__(self):
        self.metrics = {
            'first_token_latency_ms': None,
            'first_word_latency_ms': None,
            'first_audio_latency_ms': None,
            'inter_token_latencies': [],
            'token_count': 0,
            'word_count': 0,
            'audio_chunk_count': 0,
            'total_time_ms': 0
        }
        self.start_time = None
        self.first_token_time = None
        self.first_word_time = None
        self.first_audio_time = None
        self.last_token_time = None
    
    async def test_streaming_pipeline(self):
        """Test complete streaming pipeline"""
        print("🚀 Starting Ultra-Low Latency Streaming Test")
        print("=" * 60)
        
        try:
            async with websockets.connect(WS_URL) as websocket:
                # Load test audio
                audio_data = self.load_test_audio()
                
                # Send speech-to-speech request
                self.start_time = time.time()
                await self.send_audio(websocket, audio_data)
                
                # Receive and measure streaming responses
                await self.receive_streaming_responses(websocket)
                
                # Print results
                self.print_results()
                
        except Exception as e:
            print(f"❌ Test failed: {e}")
            import traceback
            traceback.print_exc()
    
    def load_test_audio(self):
        """Load test audio file"""
        if not Path(TEST_AUDIO_FILE).exists():
            print(f"⚠️ Test audio file not found: {TEST_AUDIO_FILE}")
            print("Creating synthetic test audio...")
            # Create 3 seconds of synthetic audio
            sample_rate = 16000
            duration = 3.0
            t = np.linspace(0, duration, int(sample_rate * duration))
            audio = np.sin(2 * np.pi * 440 * t) * 0.3  # 440 Hz sine wave
            sf.write(TEST_AUDIO_FILE, audio, sample_rate)
            print(f"✅ Created test audio: {TEST_AUDIO_FILE}")
        
        audio, sr = sf.read(TEST_AUDIO_FILE)
        print(f"📁 Loaded test audio: {len(audio)} samples @ {sr} Hz")
        return audio
    
    async def send_audio(self, websocket, audio_data):
        """Send audio to server"""
        # Convert to WAV bytes
        import io
        buffer = io.BytesIO()
        sf.write(buffer, audio_data, 16000, format='WAV')
        audio_bytes = buffer.getvalue()
        
        # Encode to base64
        audio_b64 = base64.b64encode(audio_bytes).decode('utf-8')
        
        # Send message
        message = {
            "type": "speech_to_speech_chunked_streaming",
            "audio_data": audio_b64,
            "conversation_id": f"test_{int(time.time() * 1000)}",
            "voice": "af_heart"
        }
        
        await websocket.send(json.dumps(message))
        print(f"📤 Sent audio data ({len(audio_b64)} chars)")
    
    async def receive_streaming_responses(self, websocket):
        """Receive and measure streaming responses"""
        print("\n📥 Receiving streaming responses...")
        print("-" * 60)
        
        try:
            while True:
                message = await asyncio.wait_for(websocket.recv(), timeout=30.0)
                data = json.loads(message)
                msg_type = data.get('type')
                
                current_time = time.time()
                
                if msg_type == 'token_chunk':
                    self.metrics['token_count'] += 1
                    token_text = data.get('text', '')
                    
                    if self.first_token_time is None:
                        self.first_token_time = current_time
                        self.metrics['first_token_latency_ms'] = (current_time - self.start_time) * 1000
                        print(f"⚡ FIRST TOKEN: {self.metrics['first_token_latency_ms']:.1f}ms - '{token_text}'")
                    
                    # Track inter-token latency
                    if self.last_token_time is not None:
                        inter_token_ms = (current_time - self.last_token_time) * 1000
                        self.metrics['inter_token_latencies'].append(inter_token_ms)
                    
                    self.last_token_time = current_time
                
                elif msg_type == 'semantic_chunk':
                    self.metrics['word_count'] += 1
                    chunk_text = data.get('text', '')
                    
                    if self.first_word_time is None:
                        self.first_word_time = current_time
                        self.metrics['first_word_latency_ms'] = (current_time - self.start_time) * 1000
                        print(f"📝 FIRST WORD: {self.metrics['first_word_latency_ms']:.1f}ms - '{chunk_text}'")
                    
                    print(f"   Word {self.metrics['word_count']}: '{chunk_text}'")
                
                elif msg_type == 'sequential_audio' or msg_type == 'chunked_streaming_audio':
                    self.metrics['audio_chunk_count'] += 1
                    
                    if self.first_audio_time is None:
                        self.first_audio_time = current_time
                        self.metrics['first_audio_latency_ms'] = (current_time - self.start_time) * 1000
                        print(f"🔊 FIRST AUDIO: {self.metrics['first_audio_latency_ms']:.1f}ms")
                    
                    text_source = data.get('text_source', '')
                    chunk_index = data.get('chunk_index', 0)
                    print(f"   Audio chunk {chunk_index}: '{text_source}'")
                
                elif msg_type == 'chunked_streaming_complete':
                    self.metrics['total_time_ms'] = (current_time - self.start_time) * 1000
                    print(f"\n✅ STREAMING COMPLETE: {self.metrics['total_time_ms']:.1f}ms")
                    break
                
                elif msg_type == 'error':
                    print(f"❌ Error: {data.get('message')}")
                    break
        
        except asyncio.TimeoutError:
            print("⏱️ Timeout waiting for response")
        except Exception as e:
            print(f"❌ Error receiving responses: {e}")
    
    def print_results(self):
        """Print test results and verification"""
        print("\n" + "=" * 60)
        print("📊 TEST RESULTS")
        print("=" * 60)
        
        # Latency metrics
        print("\n🎯 LATENCY METRICS:")
        print(f"   First Token:  {self.metrics['first_token_latency_ms']:.1f}ms (target: <{TARGET_FIRST_TOKEN_MS}ms)")
        print(f"   First Word:   {self.metrics['first_word_latency_ms']:.1f}ms (target: <{TARGET_FIRST_WORD_MS}ms)")
        print(f"   First Audio:  {self.metrics['first_audio_latency_ms']:.1f}ms (target: <{TARGET_FIRST_AUDIO_MS}ms)")
        print(f"   Total Time:   {self.metrics['total_time_ms']:.1f}ms")
        
        # Inter-token latency
        if self.metrics['inter_token_latencies']:
            avg_inter_token = sum(self.metrics['inter_token_latencies']) / len(self.metrics['inter_token_latencies'])
            max_inter_token = max(self.metrics['inter_token_latencies'])
            print(f"\n🔄 INTER-TOKEN LATENCY:")
            print(f"   Average: {avg_inter_token:.1f}ms (target: <{TARGET_INTER_TOKEN_MS}ms)")
            print(f"   Maximum: {max_inter_token:.1f}ms")
        
        # Counts
        print(f"\n📈 COUNTS:")
        print(f"   Tokens: {self.metrics['token_count']}")
        print(f"   Words:  {self.metrics['word_count']}")
        print(f"   Audio Chunks: {self.metrics['audio_chunk_count']}")
        
        # Verification
        print(f"\n✅ VERIFICATION:")
        first_token_pass = self.metrics['first_token_latency_ms'] and self.metrics['first_token_latency_ms'] < TARGET_FIRST_TOKEN_MS
        first_word_pass = self.metrics['first_word_latency_ms'] and self.metrics['first_word_latency_ms'] < TARGET_FIRST_WORD_MS
        first_audio_pass = self.metrics['first_audio_latency_ms'] and self.metrics['first_audio_latency_ms'] < TARGET_FIRST_AUDIO_MS
        
        print(f"   First Token < {TARGET_FIRST_TOKEN_MS}ms:  {'✅ PASS' if first_token_pass else '❌ FAIL'}")
        print(f"   First Word < {TARGET_FIRST_WORD_MS}ms:   {'✅ PASS' if first_word_pass else '❌ FAIL'}")
        print(f"   First Audio < {TARGET_FIRST_AUDIO_MS}ms: {'✅ PASS' if first_audio_pass else '❌ FAIL'}")
        
        if self.metrics['inter_token_latencies']:
            avg_inter_token = sum(self.metrics['inter_token_latencies']) / len(self.metrics['inter_token_latencies'])
            inter_token_pass = avg_inter_token < TARGET_INTER_TOKEN_MS
            print(f"   Avg Inter-Token < {TARGET_INTER_TOKEN_MS}ms: {'✅ PASS' if inter_token_pass else '❌ FAIL'}")
        
        # Overall result
        all_pass = first_token_pass and first_word_pass and first_audio_pass
        print(f"\n{'🎉 ALL TESTS PASSED!' if all_pass else '⚠️ SOME TESTS FAILED'}")
        print("=" * 60)

async def main():
    """Run streaming latency tests"""
    tester = StreamingLatencyTester()
    await tester.test_streaming_pipeline()

if __name__ == "__main__":
    print("Ultra-Low Latency Streaming Test")
    print("Make sure the server is running on localhost:8000")
    print()
    asyncio.run(main())

