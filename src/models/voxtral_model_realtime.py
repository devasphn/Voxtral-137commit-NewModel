"""
PRODUCTION-READY Voxtral model wrapper for CONVERSATIONAL real-time streaming
FIXED: FlashAttention2 detection, VAD implementation, silence handling
"""
import torch
import asyncio
import time
from typing import Optional, List, Dict, Any
from transformers import VoxtralForConditionalGeneration, AutoProcessor
import logging
from threading import Lock
import base64
from mistral_common.audio import Audio
from mistral_common.protocol.instruct.messages import AudioChunk, TextChunk, UserMessage
import tempfile
import soundfile as sf
import numpy as np
import os
from collections import deque
import sys

# Add current directory to Python path if not already there
current_dir = os.path.dirname(os.path.abspath(__file__))
project_root = os.path.abspath(os.path.join(current_dir, '..', '..'))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from src.utils.config import config

# Enhanced logging for real-time streaming
realtime_logger = logging.getLogger("voxtral_realtime")
realtime_logger.setLevel(logging.DEBUG)

class VoxtralModel:
    """PRODUCTION-READY Voxtral model for conversational real-time streaming with VAD"""
    
    def __init__(self):
        self.model = None
        self.processor = None
        self.audio_processor = None
        self.model_lock = Lock()
        self.is_initialized = False
        
        # Real-time streaming optimization
        self.recent_chunks = deque(maxlen=5)  # Reduced for faster processing
        self.processing_history = deque(maxlen=50)  # Reduced memory usage
        
        # Performance optimization settings
        self.device = config.model.device
        self.torch_dtype = getattr(torch, config.model.torch_dtype)
        
        # VAD and silence detection settings
        self.silence_threshold = 0.01  # Minimum energy threshold for speech
        self.min_speech_duration = 0.5  # Minimum duration (seconds) to consider as speech
        self.max_silence_duration = 2.0  # Maximum silence before stopping response
        
        # Performance optimization flags
        self.use_torch_compile = False  # Disabled by default for stability
        self.flash_attention_available = False
        
        realtime_logger.info(f"VoxtralModel initialized for {self.device} with {self.torch_dtype}")
        
    def get_audio_processor(self):
        """Lazy initialization of Audio processor"""
        if self.audio_processor is None:
            from src.models.audio_processor_realtime import AudioProcessor
            self.audio_processor = AudioProcessor()
            realtime_logger.info("Audio processor lazy-loaded into Voxtral model")
        return self.audio_processor
    
    def _check_flash_attention_availability(self):
        """
        FIXED: Properly detect FlashAttention2 availability
        """
        try:
            import flash_attn
            from flash_attn import flash_attn_func
            
            # Test if we can actually use it
            if self.device == "cuda" and torch.cuda.is_available():
                # Try to get GPU compute capability
                gpu_capability = torch.cuda.get_device_capability()
                major, minor = gpu_capability
                
                # FlashAttention2 requires compute capability >= 8.0 for optimal performance
                if major >= 8:
                    self.flash_attention_available = True
                    realtime_logger.info(f"✅ FlashAttention2 available - GPU compute capability: {major}.{minor}")
                    return "flash_attention_2"
                else:
                    realtime_logger.info(f"💡 FlashAttention2 available but GPU compute capability ({major}.{minor}) < 8.0, using eager attention")
                    return "eager"
            else:
                realtime_logger.info("💡 FlashAttention2 available but CUDA not available, using eager attention")
                return "eager"
                
        except ImportError:
            realtime_logger.info("💡 FlashAttention2 not installed, using eager attention")
            realtime_logger.info("💡 To install: pip install flash-attn --no-build-isolation")
            return "eager"
        except Exception as e:
            realtime_logger.warning(f"⚠️ FlashAttention2 check failed: {e}, using eager attention")
            return "eager"
    
    def _calculate_audio_energy(self, audio_data: np.ndarray) -> float:
        """
        Calculate RMS energy of audio signal for VAD
        """
        try:
            # Calculate RMS (Root Mean Square) energy
            rms_energy = np.sqrt(np.mean(audio_data ** 2))
            return float(rms_energy)
        except Exception as e:
            realtime_logger.warning(f"Error calculating audio energy: {e}")
            return 0.0
    
    def _is_speech_detected(self, audio_data: np.ndarray, duration_s: float) -> bool:
        """
        PRODUCTION VAD: Detect if audio contains speech using energy and duration thresholds
        """
        try:
            # Calculate audio energy
            energy = self._calculate_audio_energy(audio_data)
            
            # Apply energy threshold
            if energy < self.silence_threshold:
                realtime_logger.debug(f"🔇 Audio energy ({energy:.6f}) below silence threshold ({self.silence_threshold})")
                return False
            
            # Apply minimum duration threshold
            if duration_s < self.min_speech_duration:
                realtime_logger.debug(f"⏱️ Audio duration ({duration_s:.2f}s) below minimum speech duration ({self.min_speech_duration}s)")
                return False
            
            # Additional checks for speech-like characteristics
            # Check for spectral variation (speech has more variation than steady noise)
            spectral_variation = np.std(audio_data)
            if spectral_variation < self.silence_threshold * 0.5:
                realtime_logger.debug(f"📊 Low spectral variation ({spectral_variation:.6f}), likely not speech")
                return False
            
            realtime_logger.debug(f"🎙️ Speech detected - Energy: {energy:.6f}, Duration: {duration_s:.2f}s, Variation: {spectral_variation:.6f}")
            return True
            
        except Exception as e:
            realtime_logger.error(f"Error in speech detection: {e}")
            return False
    
    async def initialize(self):
        """Initialize the Voxtral model with FIXED attention implementation handling"""
        try:
            realtime_logger.info("🚀 Starting Voxtral model initialization for conversational streaming...")
            start_time = time.time()
            
            # Load processor
            realtime_logger.info(f"📥 Loading AutoProcessor from {config.model.name}")
            self.processor = AutoProcessor.from_pretrained(
                config.model.name,
                cache_dir=config.model.cache_dir
            )
            realtime_logger.info("✅ AutoProcessor loaded successfully")
            
            # FIXED: Determine attention implementation with proper detection
            attn_implementation = self._check_flash_attention_availability()
            realtime_logger.info(f"🔧 Using attention implementation: {attn_implementation}")
            
            # Load model with FIXED attention settings
            realtime_logger.info(f"📥 Loading Voxtral model from {config.model.name}")
            
            model_kwargs = {
                "cache_dir": config.model.cache_dir,
                "torch_dtype": self.torch_dtype,
                "device_map": "auto",
                "low_cpu_mem_usage": True,
                "trust_remote_code": True,
                "attn_implementation": attn_implementation
            }
            
            try:
                self.model = VoxtralForConditionalGeneration.from_pretrained(
                    config.model.name,
                    **model_kwargs
                )
                realtime_logger.info(f"✅ Voxtral model loaded successfully with {attn_implementation} attention")
                
            except Exception as model_load_error:
                # Fallback to eager attention
                if attn_implementation != "eager":
                    realtime_logger.warning(f"⚠️ Model loading with {attn_implementation} failed: {model_load_error}")
                    realtime_logger.info("🔄 Retrying with eager attention as fallback...")
                    
                    model_kwargs["attn_implementation"] = "eager"
                    self.model = VoxtralForConditionalGeneration.from_pretrained(
                        config.model.name,
                        **model_kwargs
                    )
                    realtime_logger.info("✅ Voxtral model loaded successfully with eager attention fallback")
                else:
                    raise model_load_error
            
            # Set model to evaluation mode
            self.model.eval()
            realtime_logger.info("🔧 Model set to evaluation mode")
            
            # FIXED: Conditional torch.compile with safety checks
            if self.use_torch_compile and hasattr(torch, 'compile') and self.device == "cuda" and not self.flash_attention_available:
                try:
                    realtime_logger.info("⚡ Attempting to compile model with torch.compile()...")
                    self.model = torch.compile(self.model, mode="default")  # Use default mode for stability
                    realtime_logger.info("✅ Model compiled successfully for faster inference")
                except Exception as e:
                    realtime_logger.warning(f"⚠️ Could not compile model: {e}")
                    realtime_logger.info("💡 Continuing without torch.compile...")
            else:
                if self.flash_attention_available:
                    realtime_logger.info("💡 Skipping torch.compile (using FlashAttention2)")
                else:
                    realtime_logger.info("💡 Skipping torch.compile (disabled for stability)")
            
            self.is_initialized = True
            init_time = time.time() - start_time
            realtime_logger.info(f"🎉 Voxtral model fully initialized in {init_time:.2f}s and ready for conversation!")
            
        except Exception as e:
            realtime_logger.error(f"❌ Failed to initialize Voxtral model: {e}")
            import traceback
            realtime_logger.error(f"❌ Full error traceback: {traceback.format_exc()}")
            raise
    
    async def process_realtime_chunk(self, audio_data: torch.Tensor, chunk_id: int, mode: str = "transcribe", prompt: str = "") -> Dict[str, Any]:
        """
        PRODUCTION-READY processing for conversational real-time audio chunks with VAD
        """
        if not self.is_initialized:
            raise RuntimeError("Model not initialized. Call initialize() first.")
        
        try:
            chunk_start_time = time.time()
            realtime_logger.debug(f"🎵 Processing conversational chunk {chunk_id} with {len(audio_data)} samples")
            
            # Convert tensor to numpy for VAD analysis
            audio_np = audio_data.detach().cpu().numpy().copy()
            sample_rate = config.audio.sample_rate
            duration_s = len(audio_np) / sample_rate
            
            # CRITICAL: Apply VAD before processing
            if not self._is_speech_detected(audio_np, duration_s):
                realtime_logger.debug(f"🔇 Chunk {chunk_id} contains no speech - skipping processing")
                return {
                    'response': '',  # Empty response for silence
                    'processing_time_ms': (time.time() - chunk_start_time) * 1000,
                    'chunk_id': chunk_id,
                    'audio_duration_s': duration_s,
                    'success': True,
                    'is_silence': True
                }
            
            with self.model_lock:
                # Store chunk in recent history
                self.recent_chunks.append({
                    'chunk_id': chunk_id,
                    'timestamp': chunk_start_time,
                    'has_speech': True
                })
                
                # Ensure audio_data is properly formatted
                if not audio_data.data.is_contiguous():
                    audio_data = audio_data.contiguous()
                
                realtime_logger.debug(f"🔊 Audio stats for chunk {chunk_id}: length={len(audio_np)}, max_val={np.max(np.abs(audio_np)):.4f}")
                
                with tempfile.NamedTemporaryFile(suffix='.wav', delete=False) as tmp_file:
                    try:
                        # Write audio to temporary file
                        sf.write(tmp_file.name, audio_np, sample_rate)
                        realtime_logger.debug(f"💾 Written chunk {chunk_id} to temporary file: {tmp_file.name}")
                        
                        # Load using mistral_common Audio
                        audio = Audio.from_file(tmp_file.name, strict=False)
                        audio_chunk = AudioChunk.from_audio(audio)
                        
                        # OPTIMIZED: Choose prompt based on mode for better conversation
                        if mode == "transcribe":
                            conversation_prompt = "Please transcribe exactly what the person is saying:"
                        elif mode == "understand":
                            if prompt:
                                conversation_prompt = prompt
                            else:
                                conversation_prompt = "Listen to this person speaking and respond naturally as if in a conversation:"
                        else:
                            conversation_prompt = "Transcribe what the person is saying:"
                        
                        # Create message format
                        text_chunk = TextChunk(text=conversation_prompt)
                        user_message = UserMessage(content=[audio_chunk, text_chunk])
                        openai_message = user_message.to_openai()
                        
                        # Process inputs
                        inputs = self.processor.apply_chat_template([openai_message], return_tensors="pt")
                        
                        # Move to device
                        if hasattr(inputs, 'to'):
                            inputs = inputs.to(self.device)
                        elif isinstance(inputs, dict):
                            inputs = {k: v.to(self.device) if hasattr(v, 'to') else v 
                                    for k, v in inputs.items()}
                        
                        realtime_logger.debug(f"🚀 Starting inference for chunk {chunk_id}")
                        inference_start = time.time()
                        
                        # PRODUCTION: Generate response with optimized settings
                        with torch.no_grad():
                            # Use mixed precision for speed
                            with torch.autocast(device_type="cuda" if "cuda" in self.device else "cpu", dtype=self.torch_dtype):
                                outputs = self.model.generate(
                                    **inputs,
                                    max_new_tokens=25,      # Reduced for speed
                                    min_new_tokens=1,       # At least some response
                                    do_sample=False,        # Greedy decoding for speed
                                    num_beams=1,           # No beam search for speed
                                    temperature=1.0,
                                    repetition_penalty=1.0,
                                    pad_token_id=self.processor.tokenizer.eos_token_id if hasattr(self.processor, 'tokenizer') else None,
                                    use_cache=True,         # Use KV cache for speed
                                    # Remove early_stopping as it's not a valid parameter
                                )
                        
                        inference_time = (time.time() - inference_start) * 1000
                        realtime_logger.debug(f"⚡ Inference completed for chunk {chunk_id} in {inference_time:.1f}ms")
                        
                        # Decode response
                        if hasattr(inputs, 'input_ids'):
                            input_length = inputs.input_ids.shape[1]
                        elif 'input_ids' in inputs:
                            input_length = inputs['input_ids'].shape[1]
                        else:
                            input_length = 0
                            
                        response = self.processor.batch_decode(
                            outputs[:, input_length:], 
                            skip_special_tokens=True
                        )[0]
                        
                        total_processing_time = (time.time() - chunk_start_time) * 1000
                        
                        # Store performance metrics
                        performance_data = {
                            'chunk_id': chunk_id,
                            'total_time_ms': total_processing_time,
                            'inference_time_ms': inference_time,
                            'audio_length_s': len(audio_np) / sample_rate,
                            'response_length': len(response),
                            'timestamp': chunk_start_time,
                            'has_speech': True
                        }
                        self.processing_history.append(performance_data)
                        
                        # Clean and optimize response
                        cleaned_response = response.strip()
                        
                        # Filter out common noise responses
                        noise_responses = [
                            "I'm not sure what you're asking",
                            "I can't understand",
                            "Could you repeat that",
                            "I didn't catch that",
                            "Yeah, I think it's a good idea"  # This seems to be a common noise response
                        ]
                        
                        # If response is too short or matches noise patterns, treat as silence
                        if len(cleaned_response) < 3 or any(noise in cleaned_response for noise in noise_responses):
                            realtime_logger.debug(f"🔇 Filtering out noise response: '{cleaned_response}'")
                            return {
                                'response': '',
                                'processing_time_ms': total_processing_time,
                                'chunk_id': chunk_id,
                                'audio_duration_s': duration_s,
                                'success': True,
                                'is_silence': True,
                                'filtered_response': cleaned_response
                            }
                        
                        if not cleaned_response:
                            cleaned_response = "[Audio processed]"
                        
                        realtime_logger.info(f"✅ Chunk {chunk_id} processed in {total_processing_time:.1f}ms: '{cleaned_response[:50]}{'...' if len(cleaned_response) > 50 else ''}'")
                        
                        return {
                            'response': cleaned_response,
                            'processing_time_ms': total_processing_time,
                            'inference_time_ms': inference_time,
                            'chunk_id': chunk_id,
                            'audio_duration_s': len(audio_np) / sample_rate,
                            'success': True,
                            'is_silence': False
                        }
                        
                    finally:
                        # Cleanup temporary file
                        try:
                            os.unlink(tmp_file.name)
                        except:
                            pass
                
        except Exception as e:
            processing_time = (time.time() - chunk_start_time) * 1000
            realtime_logger.error(f"❌ Error processing chunk {chunk_id}: {e}")
            
            # Return error response with timing info
            error_msg = "Could not process audio"
            if "CUDA out of memory" in str(e):
                error_msg = "GPU memory error"
            elif "timeout" in str(e).lower():
                error_msg = "Processing timeout"
            
            return {
                'response': error_msg,
                'processing_time_ms': processing_time,
                'chunk_id': chunk_id,
                'success': False,
                'error': str(e),
                'is_silence': False
            }

    async def transcribe_audio(self, audio_data: torch.Tensor) -> str:
        """Fast transcription for conversational chunks"""
        result = await self.process_realtime_chunk(
            audio_data, 
            chunk_id=int(time.time() * 1000),
            mode="transcribe"
        )
        return result['response']
    
    async def understand_audio(self, audio_data: torch.Tensor, question: str) -> str:
        """Audio understanding for conversational chunks"""
        result = await self.process_realtime_chunk(
            audio_data,
            chunk_id=int(time.time() * 1000),
            mode="understand",
            prompt=question
        )
        return result['response']
    
    async def process_audio_stream(self, audio_data: torch.Tensor, prompt: str = "") -> str:
        """General audio processing for conversational chunks"""
        result = await self.process_realtime_chunk(
            audio_data,
            chunk_id=int(time.time() * 1000),
            mode="transcribe" if not prompt else "understand",
            prompt=prompt
        )
        return result['response']
    
    def get_model_info(self) -> Dict[str, Any]:
        """Get enhanced model information with real-time stats"""
        base_info = {
            "status": "initialized" if self.is_initialized else "not_initialized",
            "model_name": config.model.name,
            "device": self.device,
            "torch_dtype": str(self.torch_dtype),
            "mode": "conversational_optimized",
            "flash_attention_available": self.flash_attention_available,
            "torch_compile_enabled": self.use_torch_compile,
            "vad_settings": {
                "silence_threshold": self.silence_threshold,
                "min_speech_duration": self.min_speech_duration,
                "max_silence_duration": self.max_silence_duration
            }
        }
        
        if self.is_initialized and self.processing_history:
            # Calculate real-time performance stats
            recent_history = list(self.processing_history)[-10:]  # Last 10 chunks
            if recent_history:
                avg_processing_time = np.mean([h['total_time_ms'] for h in recent_history])
                avg_inference_time = np.mean([h['inference_time_ms'] for h in recent_history])
                total_chunks = len(self.processing_history)
                speech_chunks = len([h for h in recent_history if h.get('has_speech', False)])
                
                base_info.update({
                    "realtime_stats": {
                        "total_chunks_processed": total_chunks,
                        "speech_chunks_in_recent_10": speech_chunks,
                        "avg_processing_time_ms": round(avg_processing_time, 1),
                        "avg_inference_time_ms": round(avg_inference_time, 1),
                        "recent_chunks_in_memory": len(self.recent_chunks),
                        "performance_history_size": len(self.processing_history)
                    }
                })
        
        return base_info

# Global model instance for real-time streaming
voxtral_model = VoxtralModel()

# FIXED: Add proper main execution block for testing
if __name__ == "__main__":
    import asyncio
    
    async def test_model():
        """Test model initialization and basic functionality"""
        print("🧪 Testing Voxtral Conversational Model with VAD...")
        
        try:
            # Initialize model
            await voxtral_model.initialize()
            
            # Test with dummy audio (silence)
            silent_audio = torch.zeros(16000) + 0.001  # Very quiet audio
            result = await voxtral_model.process_realtime_chunk(silent_audio, 1)
            print(f"Silent audio result: {result}")
            
            # Test with dummy audio (louder)
            loud_audio = torch.randn(16000) * 0.1  # Louder audio
            result = await voxtral_model.process_realtime_chunk(loud_audio, 2)
            print(f"Loud audio result: {result}")
            
            print(f"✅ Test completed successfully")
            print(f"📊 Model info: {voxtral_model.get_model_info()}")
            
        except Exception as e:
            print(f"❌ Test failed: {e}")
            import traceback
            traceback.print_exc()
    
    asyncio.run(test_model())
