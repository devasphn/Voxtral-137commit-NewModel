#!/usr/bin/env python3
"""
Complete End-to-End Pipeline Test for Chunked Streaming
Tests the actual speech-to-speech pipeline with realistic audio input
"""

import asyncio
import time
import numpy as np
import logging
import json
import base64
import websockets
import soundfile as sf
import tempfile
import os
from typing import List, Dict, Any

# Setup logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
test_logger = logging.getLogger("complete_pipeline_test")

class CompletePipelineTest:
    """Complete end-to-end pipeline test with chunked streaming"""
    
    def __init__(self):
        self.test_results = {}
        self.websocket_url = "ws://localhost:8000/ws"
        self.server_url = "http://localhost:8000"
        
    def generate_test_audio(self, text: str = "Tell me about Hyderabad in detail", duration: float = 3.0) -> np.ndarray:
        """Generate realistic test audio that simulates speech"""
        sample_rate = 16000
        t = np.linspace(0, duration, int(sample_rate * duration))
        
        # Create a more realistic speech-like signal
        # Multiple frequency components to simulate formants
        signal = (
            0.3 * np.sin(2 * np.pi * 150 * t) +  # Fundamental frequency
            0.2 * np.sin(2 * np.pi * 300 * t) +  # First harmonic
            0.1 * np.sin(2 * np.pi * 450 * t) +  # Second harmonic
            0.05 * np.sin(2 * np.pi * 600 * t)   # Third harmonic
        )
        
        # Add amplitude modulation to simulate speech patterns
        envelope = 0.5 * (1 + np.sin(2 * np.pi * 3 * t))  # 3 Hz modulation
        signal = signal * envelope
        
        # Add some noise for realism
        noise = np.random.normal(0, 0.02, signal.shape)
        signal = signal + noise
        
        # Normalize
        signal = signal / np.max(np.abs(signal)) * 0.8
        
        return signal.astype(np.float32)
    
    async def test_pipeline_direct(self):
        """Test the pipeline directly without WebSocket"""
        test_logger.info("🧪 Testing Pipeline Direct (No WebSocket)")
        
        try:
            from src.models.speech_to_speech_pipeline import SpeechToSpeechPipeline
            
            # Initialize pipeline
            pipeline = SpeechToSpeechPipeline()
            await pipeline.initialize()
            
            # Generate test audio
            test_audio = self.generate_test_audio("Tell me about Hyderabad in detail", 3.0)
            test_logger.info(f"🎙️ Generated test audio: {len(test_audio)} samples, {len(test_audio)/16000:.1f}s")
            
            # Test chunked streaming pipeline
            start_time = time.time()
            first_chunk_time = None
            first_audio_time = None
            chunks_received = []
            audio_chunks_received = []
            
            async for chunk_data in pipeline.process_conversation_turn_chunked_streaming(
                test_audio,
                conversation_id="direct_test",
                voice_preference="af_heart"
            ):
                chunk_type = chunk_data.get('type')
                current_time = time.time()
                
                if chunk_type == 'text_chunk':
                    if first_chunk_time is None:
                        first_chunk_time = current_time
                        first_chunk_latency = (first_chunk_time - start_time) * 1000
                        test_logger.info(f"⚡ First text chunk: {first_chunk_latency:.1f}ms - '{chunk_data.get('text', '')}'")
                    
                    chunks_received.append(chunk_data)
                    test_logger.info(f"📝 Text chunk {len(chunks_received)}: '{chunk_data.get('text', '')}' "
                                   f"({chunk_data.get('boundary_type', 'unknown')})")
                
                elif chunk_type == 'audio_chunk':
                    if first_audio_time is None:
                        first_audio_time = current_time
                        first_audio_latency = (first_audio_time - start_time) * 1000
                        test_logger.info(f"🎵 First audio chunk: {first_audio_latency:.1f}ms")
                    
                    audio_chunks_received.append(chunk_data)
                    audio_data = chunk_data.get('audio_data')
                    if audio_data is not None:
                        test_logger.info(f"🔊 Audio chunk {len(audio_chunks_received)}: {len(audio_data)} samples "
                                       f"from '{chunk_data.get('text_source', '')}'")
                
                elif chunk_type == 'complete':
                    total_time = (current_time - start_time) * 1000
                    test_logger.info(f"✅ Pipeline complete: {total_time:.1f}ms")
                    test_logger.info(f"📊 Total chunks: {len(chunks_received)} text, {len(audio_chunks_received)} audio")
                    break
                
                elif chunk_type == 'error':
                    test_logger.error(f"❌ Pipeline error: {chunk_data.get('error')}")
                    return False
            
            # Evaluate results
            if first_chunk_time and first_audio_time:
                first_chunk_latency = (first_chunk_time - start_time) * 1000
                first_audio_latency = (first_audio_time - start_time) * 1000
                
                test_logger.info(f"📊 Performance Summary:")
                test_logger.info(f"   First text chunk: {first_chunk_latency:.1f}ms")
                test_logger.info(f"   First audio chunk: {first_audio_latency:.1f}ms")
                test_logger.info(f"   Text chunks: {len(chunks_received)}")
                test_logger.info(f"   Audio chunks: {len(audio_chunks_received)}")
                
                # Check if we meet the <500ms target
                meets_target = first_audio_latency < 500
                test_logger.info(f"🎯 Target (<500ms): {'✅ PASSED' if meets_target else '❌ FAILED'}")
                
                self.test_results['direct_pipeline'] = {
                    'success': True,
                    'first_chunk_latency_ms': first_chunk_latency,
                    'first_audio_latency_ms': first_audio_latency,
                    'text_chunks': len(chunks_received),
                    'audio_chunks': len(audio_chunks_received),
                    'meets_target': meets_target
                }
                
                return meets_target
            else:
                test_logger.error("❌ No chunks received")
                return False
                
        except Exception as e:
            test_logger.error(f"❌ Direct pipeline test failed: {e}")
            import traceback
            test_logger.error(f"❌ Traceback: {traceback.format_exc()}")
            return False
    
    async def test_websocket_pipeline(self):
        """Test the complete pipeline through WebSocket interface"""
        test_logger.info("🧪 Testing WebSocket Pipeline")
        
        try:
            # Generate test audio
            test_audio = self.generate_test_audio("Tell me about Hyderabad in detail", 3.0)
            
            # Save to temporary file for WebSocket transmission
            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp_file:
                sf.write(tmp_file.name, test_audio, 16000)
                
                # Read as bytes for WebSocket
                with open(tmp_file.name, 'rb') as f:
                    audio_bytes = f.read()
                
                # Clean up
                os.unlink(tmp_file.name)
            
            # Connect to WebSocket
            start_time = time.time()
            first_response_time = None
            first_audio_time = None
            responses_received = []
            
            async with websockets.connect(self.websocket_url) as websocket:
                test_logger.info("🔗 Connected to WebSocket")
                
                # Send audio data
                message = {
                    "type": "speech_to_speech",
                    "audio_data": base64.b64encode(audio_bytes).decode('utf-8'),
                    "conversation_id": "websocket_test",
                    "voice": "af_heart"
                }
                
                await websocket.send(json.dumps(message))
                test_logger.info("📤 Sent audio data to WebSocket")
                
                # Listen for responses
                timeout_count = 0
                max_timeout = 30  # 30 second total timeout
                
                while timeout_count < max_timeout:
                    try:
                        response = await asyncio.wait_for(websocket.recv(), timeout=1.0)
                        data = json.loads(response)
                        current_time = time.time()
                        
                        response_type = data.get('type')
                        
                        if response_type in ['semantic_chunk', 'text_chunk', 'streaming_words']:
                            if first_response_time is None:
                                first_response_time = current_time
                                first_response_latency = (first_response_time - start_time) * 1000
                                test_logger.info(f"⚡ First response: {first_response_latency:.1f}ms - '{data.get('text', '')}'")
                            
                            responses_received.append(data)
                            test_logger.info(f"📝 Response {len(responses_received)}: '{data.get('text', '')}'")
                        
                        elif response_type in ['streaming_audio', 'chunked_streaming_audio', 'audio_chunk']:
                            if first_audio_time is None:
                                first_audio_time = current_time
                                first_audio_latency = (first_audio_time - start_time) * 1000
                                test_logger.info(f"🎵 First audio: {first_audio_latency:.1f}ms")
                            
                            test_logger.info(f"🔊 Audio chunk received from: '{data.get('text_source', '')}'")
                        
                        elif response_type in ['streaming_complete', 'chunked_streaming_complete', 'complete']:
                            total_time = (current_time - start_time) * 1000
                            test_logger.info(f"✅ WebSocket pipeline complete: {total_time:.1f}ms")
                            break
                        
                        elif response_type == 'error':
                            test_logger.error(f"❌ WebSocket error: {data.get('error', 'Unknown error')}")
                            return False
                        
                        elif response_type == 'processing':
                            test_logger.info(f"🔄 Processing: {data.get('message', '')}")
                        
                        timeout_count = 0  # Reset timeout on successful message
                        
                    except asyncio.TimeoutError:
                        timeout_count += 1
                        if timeout_count >= max_timeout:
                            test_logger.error("❌ WebSocket timeout - no response received")
                            return False
            
            # Evaluate WebSocket results
            if first_response_time and first_audio_time:
                first_response_latency = (first_response_time - start_time) * 1000
                first_audio_latency = (first_audio_time - start_time) * 1000
                
                test_logger.info(f"📊 WebSocket Performance Summary:")
                test_logger.info(f"   First response: {first_response_latency:.1f}ms")
                test_logger.info(f"   First audio: {first_audio_latency:.1f}ms")
                test_logger.info(f"   Total responses: {len(responses_received)}")
                
                meets_target = first_audio_latency < 500
                test_logger.info(f"🎯 Target (<500ms): {'✅ PASSED' if meets_target else '❌ FAILED'}")
                
                self.test_results['websocket_pipeline'] = {
                    'success': True,
                    'first_response_latency_ms': first_response_latency,
                    'first_audio_latency_ms': first_audio_latency,
                    'total_responses': len(responses_received),
                    'meets_target': meets_target
                }
                
                return meets_target
            else:
                test_logger.error("❌ No responses received from WebSocket")
                return False
                
        except Exception as e:
            test_logger.error(f"❌ WebSocket pipeline test failed: {e}")
            import traceback
            test_logger.error(f"❌ Traceback: {traceback.format_exc()}")
            return False
    
    async def run_all_tests(self):
        """Run all pipeline tests"""
        test_logger.info("🚀 Starting Complete Pipeline Tests")
        test_logger.info("=" * 60)
        
        tests = [
            ("Direct Pipeline Test", self.test_pipeline_direct),
            ("WebSocket Pipeline Test", self.test_websocket_pipeline)
        ]
        
        results = {}
        
        for test_name, test_func in tests:
            test_logger.info(f"\n🧪 Running {test_name}...")
            try:
                result = await test_func()
                results[test_name] = result
                status = "✅ PASSED" if result else "❌ FAILED"
                test_logger.info(f"  {status}")
            except Exception as e:
                results[test_name] = False
                test_logger.error(f"  ❌ FAILED: {e}")
        
        # Summary
        test_logger.info("\n" + "=" * 60)
        test_logger.info("📊 COMPLETE PIPELINE TEST RESULTS")
        test_logger.info("=" * 60)
        
        passed = sum(1 for result in results.values() if result)
        total = len(results)
        
        for test_name, result in results.items():
            status = "✅ PASSED" if result else "❌ FAILED"
            test_logger.info(f"  {test_name}: {status}")
        
        test_logger.info(f"\n🎯 Overall: {passed}/{total} tests passed")
        
        # Detailed results
        if self.test_results:
            test_logger.info("\n📈 Detailed Performance Results:")
            for test_name, metrics in self.test_results.items():
                test_logger.info(f"  {test_name}:")
                for key, value in metrics.items():
                    if isinstance(value, float):
                        test_logger.info(f"    {key}: {value:.1f}")
                    else:
                        test_logger.info(f"    {key}: {value}")
        
        if passed == total:
            test_logger.info("🎉 All tests passed! Chunked streaming pipeline is working correctly.")
            return True
        else:
            test_logger.info("⚠️ Some tests failed. Pipeline needs debugging.")
            return False

async def main():
    """Main test runner"""
    test = CompletePipelineTest()
    success = await test.run_all_tests()
    return success

if __name__ == "__main__":
    success = asyncio.run(main())
    exit(0 if success else 1)
