"""
PRODUCTION-READY Voxtral model wrapper for CONVERSATIONAL real-time streaming
FIXED: FlashAttention2 detection, VAD implementation, silence handling
"""
import torch
import asyncio
import time
from typing import Optional, List, Dict, Any
# Import with compatibility layer
try:
    from transformers import VoxtralForConditionalGeneration, AutoProcessor
    VOXTRAL_AVAILABLE = True
except ImportError:
    from src.utils.compatibility import FallbackVoxtralModel
    VoxtralForConditionalGeneration = FallbackVoxtralModel
    AutoProcessor = None
    VOXTRAL_AVAILABLE = False

import logging
from threading import Lock
import base64

# Import semantic chunking utility
from src.utils.semantic_chunking import semantic_chunker, SemanticChunk

# NOTE: mistral_common imports removed - using standard Hugging Face VoxtralProcessor API
# The official VoxtralProcessor uses standard conversation format, not mistral_common classes
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

# Import config with fallback
try:
    from src.utils.config import config
except ImportError:
    from src.utils.compatibility import get_config
    config = get_config()

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
        
        # ULTRA-LOW LATENCY Performance optimization settings
        self.device = config.model.device
        # OPTIMIZATION: Use float16 for inference to reduce memory and increase speed
        # FIXED: Proper dtype logic - prioritize float16 for maximum performance
        if config.model.torch_dtype == "float16":
            self.torch_dtype = torch.float16
        elif config.model.torch_dtype == "bfloat16":
            self.torch_dtype = torch.bfloat16
        elif config.model.torch_dtype == "float32":
            self.torch_dtype = torch.float32
        else:
            # Default to float16 for best performance
            self.torch_dtype = torch.float16

        # CALIBRATED VAD and silence detection settings - Perfectly aligned with AudioProcessor
        self.silence_threshold = 0.015  # Aligned with AudioProcessor threshold for perfect compatibility
        self.min_speech_duration = 0.4  # Aligned with AudioProcessor minimum duration (400ms)
        self.max_silence_duration = 1.2  # Aligned with AudioProcessor silence duration (1200ms)

        # ULTRA-LOW LATENCY Performance optimization flags
        self.use_torch_compile = True   # ENABLED: For maximum performance optimization
        self.flash_attention_available = False
        self.use_kv_cache_optimization = True  # ADDED: Advanced KV cache optimization
        self.use_memory_efficient_attention = True  # ADDED: Memory efficient attention
        self.use_gradient_checkpointing = False  # DISABLED: Not needed for inference
        self.use_scaled_dot_product_attention = True  # ENABLED: PyTorch 2.0+ optimization
        self.enable_gpu_memory_optimization = True  # ENABLED: GPU memory optimizations

        # ULTRA-LOW LATENCY: Caching for repeated operations
        self._processor_cache = {}
        self._attention_cache = {}
        self._generation_cache = {}

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

            # ULTRA-LOW LATENCY: GPU memory optimization setup
            if self.enable_gpu_memory_optimization and self.device == "cuda" and torch.cuda.is_available():
                try:
                    torch.cuda.empty_cache()
                    torch.cuda.set_per_process_memory_fraction(0.95)  # Use 95% of GPU memory
                    # Enable memory pool for faster allocation
                    if hasattr(torch.cuda, 'memory_pool'):
                        torch.cuda.memory_pool.set_memory_fraction(0.95)
                    realtime_logger.info("🚀 GPU memory optimization enabled")
                except Exception as e:
                    realtime_logger.warning(f"⚠️ GPU memory optimization failed: {e}")
                    realtime_logger.info("💡 Continuing without GPU memory optimization...")
            elif self.device == "cuda" and not torch.cuda.is_available():
                realtime_logger.warning("⚠️ CUDA device specified but CUDA not available, falling back to CPU")
                self.device = "cpu"
            
            # Load processor with authentication
            realtime_logger.info(f"📥 Loading AutoProcessor from {config.model.name}")

            processor_kwargs = {
                "cache_dir": config.model.cache_dir,
                "trust_remote_code": True
            }

            # FIXED: Add authentication token if available
            import os
            if os.getenv('HF_TOKEN'):
                processor_kwargs["token"] = os.getenv('HF_TOKEN')
                realtime_logger.info("🔑 Using HuggingFace authentication token for processor")

            self.processor = AutoProcessor.from_pretrained(
                config.model.name,
                **processor_kwargs
            )
            realtime_logger.info("✅ AutoProcessor loaded successfully")
            
            # FIXED: Determine attention implementation with proper detection
            attn_implementation = self._check_flash_attention_availability()
            realtime_logger.info(f"🔧 Using attention implementation: {attn_implementation}")
            
            # Load model with FIXED attention settings
            realtime_logger.info(f"📥 Loading Voxtral model from {config.model.name}")
            
            # FIXED: Updated model loading parameters for compatibility
            model_kwargs = {
                "cache_dir": config.model.cache_dir,
                "dtype": self.torch_dtype,  # FIXED: Use 'dtype' instead of deprecated 'torch_dtype'
                "device_map": "auto",
                "low_cpu_mem_usage": True,
                "trust_remote_code": True,
                "attn_implementation": attn_implementation,
                # ULTRA-LOW LATENCY: Additional optimization parameters
                "use_safetensors": True,  # Faster loading
                # REMOVED: variant parameter that may cause loading issues
            }

            # FIXED: Add authentication token if available
            import os
            if os.getenv('HF_TOKEN'):
                model_kwargs["token"] = os.getenv('HF_TOKEN')
                realtime_logger.info("🔑 Using HuggingFace authentication token")
            
            try:
                realtime_logger.info(f"🔄 Loading Voxtral model with dtype={self.torch_dtype}, attention={attn_implementation}")
                self.model = VoxtralForConditionalGeneration.from_pretrained(
                    config.model.name,
                    **model_kwargs
                )
                realtime_logger.info(f"✅ Voxtral model loaded successfully with {attn_implementation} attention")

            except Exception as model_load_error:
                realtime_logger.error(f"❌ Initial model loading failed: {model_load_error}")

                # ENHANCED: Multiple fallback strategies for robust loading
                if attn_implementation != "eager":
                    realtime_logger.warning(f"⚠️ Model loading with {attn_implementation} failed: {model_load_error}")
                    realtime_logger.info("🔄 Retrying with eager attention as fallback...")

                    model_kwargs["attn_implementation"] = "eager"
                    try:
                        self.model = VoxtralForConditionalGeneration.from_pretrained(
                            config.model.name,
                            **model_kwargs
                        )
                        realtime_logger.info("✅ Voxtral model loaded successfully with eager attention fallback")
                    except Exception as eager_error:
                        realtime_logger.error(f"❌ Eager attention fallback also failed: {eager_error}")
                        # Try without safetensors as final fallback
                        realtime_logger.info("🔄 Trying final fallback without safetensors...")
                        model_kwargs["use_safetensors"] = False
                        self.model = VoxtralForConditionalGeneration.from_pretrained(
                            config.model.name,
                            **model_kwargs
                        )
                        realtime_logger.info("✅ Model loaded successfully without safetensors")

                elif "safetensors" in str(model_load_error).lower() or "num" in str(model_load_error).lower():
                    realtime_logger.warning(f"⚠️ Safetensors loading failed: {model_load_error}")
                    realtime_logger.info("🔄 Retrying without safetensors...")

                    model_kwargs["use_safetensors"] = False
                    self.model = VoxtralForConditionalGeneration.from_pretrained(
                        config.model.name,
                        **model_kwargs
                    )
                    realtime_logger.info("✅ Model loaded successfully without safetensors")

                else:
                    # Generic fallback - try with minimal parameters
                    realtime_logger.warning(f"⚠️ Generic model loading error: {model_load_error}")
                    realtime_logger.info("🔄 Trying minimal parameter fallback...")

                    minimal_kwargs = {
                        "cache_dir": config.model.cache_dir,
                        "dtype": self.torch_dtype,
                        "device_map": "auto",
                        "trust_remote_code": True,
                        "use_safetensors": False,
                        "attn_implementation": "eager"
                    }

                    # Add token if available
                    if os.getenv('HF_TOKEN'):
                        minimal_kwargs["token"] = os.getenv('HF_TOKEN')

                    self.model = VoxtralForConditionalGeneration.from_pretrained(
                        config.model.name,
                        **minimal_kwargs
                    )
                    realtime_logger.info("✅ Model loaded successfully with minimal parameters")
            
            # Set model to evaluation mode
            self.model.eval()
            realtime_logger.info("🔧 Model set to evaluation mode")
            
            # ULTRA-LOW LATENCY: Fixed torch.compile optimization (no mode/options conflict)
            if self.use_torch_compile and hasattr(torch, 'compile'):
                try:
                    realtime_logger.info("⚡ Attempting ULTRA-LOW LATENCY model compilation...")

                    # Set optimal compilation environment
                    import os
                    os.environ['TORCH_COMPILE_DEBUG'] = '0'  # Disable debug for speed
                    os.environ['TORCHINDUCTOR_CACHE_DIR'] = './torch_compile_cache'
                    os.environ['PYTORCH_CUDA_ALLOC_CONF'] = 'max_split_size_mb:512,roundup_power2_divisions:16'

                    # FIXED: Method 1 - Use mode-based compilation (no options conflict)
                    try:
                        self.model = torch.compile(
                            self.model,
                            mode="reduce-overhead",  # Maximum performance mode
                            fullgraph=True,         # Compile entire graph
                            dynamic=False           # Static shapes for speed
                        )
                        realtime_logger.info("✅ Model compiled with reduce-overhead mode + CUDA optimizations")
                    except Exception as mode_error:
                        realtime_logger.warning(f"⚠️ Mode compilation failed: {mode_error}")

                        # FIXED: Method 2 - Use options-based compilation (no mode conflict)
                        try:
                            if self.device == "cuda" and torch.cuda.is_available():
                                self.model = torch.compile(
                                    self.model,
                                    options={
                                        "triton.cudagraphs": True,
                                        "max_autotune": True,
                                        "epilogue_fusion": True,
                                        "max_autotune_gemm": True,
                                    }
                                )
                                realtime_logger.info("✅ Model compiled with CUDA-optimized options")
                            else:
                                # CPU fallback
                                self.model = torch.compile(self.model)
                                realtime_logger.info("✅ Model compiled with basic optimizations")
                        except Exception as options_error:
                            realtime_logger.warning(f"⚠️ Options compilation failed: {options_error}")
                            # Final fallback to basic compilation
                            self.model = torch.compile(self.model)
                            realtime_logger.info("✅ Model compiled with basic optimizations")

                except Exception as e:
                    realtime_logger.warning(f"⚠️ All torch.compile attempts failed: {e}")
                    try:
                        # Fallback to max-autotune mode
                        realtime_logger.info("🔄 Falling back to max-autotune compilation mode...")
                        self.model = torch.compile(self.model, mode="max-autotune")
                        realtime_logger.info("✅ Model compiled with max-autotune optimizations")
                    except Exception as e2:
                        realtime_logger.warning(f"⚠️ Max-autotune compilation also failed: {e2}")
                        try:
                            # Final fallback to default mode
                            realtime_logger.info("🔄 Final fallback to default compilation mode...")
                            self.model = torch.compile(self.model, mode="default")
                            realtime_logger.info("✅ Model compiled with default optimizations")
                        except Exception as e3:
                            realtime_logger.warning(f"⚠️ All compilation modes failed: {e3}")
                            realtime_logger.info("💡 Continuing without torch.compile...")
            else:
                realtime_logger.info("💡 torch.compile disabled or not available")

            # ULTRA-LOW LATENCY: CUDA Graphs and Memory Optimization
            if torch.cuda.is_available():
                torch.backends.cudnn.benchmark = True
                torch.backends.cudnn.deterministic = False
                torch.backends.cuda.matmul.allow_tf32 = True
                torch.backends.cudnn.allow_tf32 = True

                # Enable memory optimization
                torch.cuda.empty_cache()
                torch.cuda.set_per_process_memory_fraction(0.95)

                realtime_logger.info("🚀 CUDA optimizations enabled for maximum performance")

            # ULTRA-LOW LATENCY: Additional model optimizations
            if hasattr(self.model, 'generation_config'):
                # Optimize generation config for speed
                self.model.generation_config.use_cache = True
                self.model.generation_config.pad_token_id = self.processor.tokenizer.eos_token_id
                # ULTRA-OPTIMIZED generation parameters for sub-50ms target
                self.model.generation_config.do_sample = False  # Greedy decoding for maximum speed
                self.model.generation_config.temperature = 0.0  # No temperature for maximum speed
                self.model.generation_config.top_p = 1.0  # No top-p filtering for speed
                self.model.generation_config.top_k = 1  # Greedy selection only
                self.model.generation_config.repetition_penalty = 1.0  # No penalty overhead
                self.model.generation_config.num_beams = 1  # No beam search for speed
                self.model.generation_config.early_stopping = False  # No early stopping overhead
                self.model.generation_config.max_new_tokens = 150  # Allow complete sentences
                self.model.generation_config.min_new_tokens = 1  # Allow single token responses
                realtime_logger.info("✅ Generation config optimized for ultra-low latency")

            # ULTRA-LOW LATENCY: Enable PyTorch 2.0+ optimizations
            if hasattr(torch.nn.functional, 'scaled_dot_product_attention') and self.use_scaled_dot_product_attention:
                # This enables Flash Attention or other optimized attention implementations
                torch.backends.cuda.enable_flash_sdp(True)
                torch.backends.cuda.enable_math_sdp(True)
                torch.backends.cuda.enable_mem_efficient_sdp(True)
                realtime_logger.info("✅ PyTorch 2.0+ scaled dot product attention optimizations enabled")

            # ULTRA-LOW LATENCY: Set optimal inference settings
            if self.device == "cuda" and torch.cuda.is_available():
                try:
                    torch.backends.cudnn.benchmark = True  # Optimize for consistent input sizes
                    torch.backends.cudnn.deterministic = False  # Allow non-deterministic for speed
                    torch.backends.cuda.matmul.allow_tf32 = True  # Enable TF32 for speed
                    torch.backends.cudnn.allow_tf32 = True
                    realtime_logger.info("✅ CUDA optimizations enabled for maximum performance")
                except Exception as e:
                    realtime_logger.warning(f"⚠️ CUDA optimizations failed: {e}")
                    realtime_logger.info("💡 Continuing without CUDA optimizations...")
            else:
                realtime_logger.info("💡 CUDA not available, using CPU optimizations")
            
            self.is_initialized = True
            init_time = time.time() - start_time
            realtime_logger.info(f"🎉 Voxtral model fully initialized in {init_time:.2f}s and ready for conversation!")

            # COLD-START OPTIMIZATION: Pre-warm the model with dummy inference
            await self._prewarm_model()
            
        except Exception as e:
            realtime_logger.error(f"❌ Failed to initialize Voxtral model: {e}")
            import traceback
            realtime_logger.error(f"❌ Full error traceback: {traceback.format_exc()}")
            raise

    async def _prewarm_model(self):
        """
        COLD-START OPTIMIZATION: Pre-warm the model with dummy inference to reduce first-request latency
        """
        try:
            prewarm_start = time.time()
            realtime_logger.info("🔥 Pre-warming model for optimal cold-start performance...")

            # Create dummy audio input (1 second of silence)
            dummy_audio = torch.zeros(16000, dtype=torch.float32)
            if torch.cuda.is_available():
                dummy_audio = dummy_audio.cuda()

            # Create a temporary file for dummy audio
            import tempfile
            import soundfile as sf
            with tempfile.NamedTemporaryFile(suffix='.wav', delete=False) as tmp_file:
                sf.write(tmp_file.name, dummy_audio.cpu().numpy(), 16000)

                # Create dummy conversation for pre-warming
                dummy_conversation = [
                    {
                        "role": "user",
                        "content": [
                            {"type": "audio", "path": tmp_file.name},
                            {"type": "text", "text": "Hi"}
                        ]
                    }
                ]

                # Process dummy conversation to warm up the pipeline
                dummy_inputs = self.processor.apply_chat_template(dummy_conversation, return_tensors="pt")

                # Move to device
                if torch.cuda.is_available():
                    if hasattr(dummy_inputs, 'to'):
                        dummy_inputs = dummy_inputs.to(self.device)
                    elif isinstance(dummy_inputs, dict):
                        dummy_inputs = {k: v.to(self.device) if hasattr(v, 'to') else v for k, v in dummy_inputs.items()}

                # Warm up the model with a single forward pass
                with torch.no_grad():
                    # Use the uncompiled model for pre-warming to avoid compilation overhead
                    model_to_use = self.model._orig_mod if hasattr(self.model, '_orig_mod') else self.model

                    # Single forward pass to warm up CUDA kernels and memory allocation
                    outputs = model_to_use(**dummy_inputs)

                    # Warm up token generation
                    dummy_logits = outputs.logits[0, -1, :]
                    dummy_token = torch.argmax(dummy_logits, dim=-1, keepdim=True)

                    # Warm up tokenizer
                    if hasattr(self.processor, 'tokenizer'):
                        dummy_text = self.processor.tokenizer.decode([dummy_token.item()], skip_special_tokens=True)

                # Clean up temporary file
                import os
                os.unlink(tmp_file.name)

            # Clear cache to free memory
            if torch.cuda.is_available():
                torch.cuda.empty_cache()

            prewarm_time = (time.time() - prewarm_start) * 1000
            realtime_logger.info(f"🔥 Model pre-warming completed in {prewarm_time:.1f}ms - ready for ultra-low latency!")

        except Exception as e:
            realtime_logger.warning(f"⚠️ Model pre-warming failed: {e} - continuing without pre-warming")

    async def process_realtime_chunk(self, audio_data: torch.Tensor, chunk_id: int, mode: str = "conversation", prompt: str = "") -> Dict[str, Any]:
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
                        
                        # FIXED: Use standard Hugging Face VoxtralProcessor API
                        # Choose processing mode based on requirements
                        if mode == "speech_to_speech" or not prompt:
                            # Audio-only mode for pure speech-to-speech processing
                            conversation = [
                                {
                                    "role": "user",
                                    "content": [
                                        {"type": "audio", "path": tmp_file.name}
                                    ]
                                }
                            ]
                            realtime_logger.debug(f"🎙️ Using audio-only mode for speech-to-speech processing")
                        else:
                            # Audio + text mode for conversational AI
                            # OPTIMIZED: Friendly conversational prompt without markdown formatting
                            conversation_prompt = prompt or "You are a helpful and friendly AI assistant. Respond naturally in conversation without using any markdown formatting, asterisks, or special characters. Keep responses conversational and complete but concise. Speak as if you're having a natural chat with a friend."

                            conversation = [
                                {
                                    "role": "user",
                                    "content": [
                                        {"type": "audio", "path": tmp_file.name},
                                        {"type": "text", "text": conversation_prompt}
                                    ]
                                }
                            ]
                            realtime_logger.debug(f"🎙️ Using audio+text mode for conversational AI")

                        # Process inputs with correct VoxtralProcessor API
                        inputs = self.processor.apply_chat_template(conversation, return_tensors="pt")
                        
                        # Move to device
                        if hasattr(inputs, 'to'):
                            inputs = inputs.to(self.device)
                        elif isinstance(inputs, dict):
                            inputs = {k: v.to(self.device) if hasattr(v, 'to') else v 
                                    for k, v in inputs.items()}
                        
                        realtime_logger.debug(f"🚀 Starting inference for chunk {chunk_id}")
                        inference_start = time.time()

                        # ENHANCED: Generate response with timeout and real-time optimizations
                        try:
                            with torch.no_grad():
                                # Use mixed precision for speed
                                with torch.autocast(device_type="cuda" if "cuda" in self.device else "cpu", dtype=self.torch_dtype, enabled=True):
                                    # STREAMING: Check if streaming mode is requested
                                    streaming_mode = mode == "streaming" or prompt.get("streaming", False) if isinstance(prompt, dict) else False

                                    if streaming_mode:
                                        # STREAMING GENERATION: 250 tokens with word-level streaming
                                        outputs = self.model.generate(
                                            **inputs,
                                            max_new_tokens=250,     # STREAMING: Full response length
                                            min_new_tokens=1,
                                            do_sample=True,         # STREAMING: Enable sampling for natural responses
                                            num_beams=1,
                                            temperature=0.3,        # STREAMING: Balanced temperature for quality
                                            top_p=0.9,             # STREAMING: Broader sampling for natural speech
                                            top_k=50,              # STREAMING: Expanded vocabulary
                                            repetition_penalty=1.2,
                                            pad_token_id=self.processor.tokenizer.eos_token_id if hasattr(self.processor, 'tokenizer') else None,
                                            eos_token_id=self.processor.tokenizer.eos_token_id if hasattr(self.processor, 'tokenizer') else None,
                                            use_cache=True,
                                            early_stopping=True,
                                            output_scores=False,
                                            output_attentions=False,
                                            output_hidden_states=False,
                                            return_dict_in_generate=False,
                                            synced_gpus=False,
                                        )
                                    else:
                                        # ULTRA-LOW LATENCY: Maximum speed generation parameters for sub-50ms target
                                        outputs = self.model.generate(
                                            **inputs,
                                            max_new_tokens=150,     # Allow complete sentences
                                            min_new_tokens=1,       # OPTIMIZED: Allow single token responses
                                            do_sample=False,        # OPTIMIZED: Greedy decoding for maximum speed
                                            num_beams=1,           # Keep single beam for speed
                                            temperature=0.0,       # OPTIMIZED: No temperature for maximum speed
                                            top_p=1.0,            # OPTIMIZED: No top-p filtering for speed
                                            top_k=1,              # OPTIMIZED: Greedy selection only
                                            repetition_penalty=1.0, # OPTIMIZED: No penalty overhead
                                            pad_token_id=self.processor.tokenizer.eos_token_id if hasattr(self.processor, 'tokenizer') else None,
                                            eos_token_id=self.processor.tokenizer.eos_token_id if hasattr(self.processor, 'tokenizer') else None,
                                            use_cache=True,         # Use KV cache for speed
                                            # ULTRA-LOW LATENCY: Additional optimizations
                                            early_stopping=False,  # OPTIMIZED: No early stopping overhead
                                            output_scores=False,   # Don't compute scores for speed
                                            output_attentions=False, # Don't compute attentions for speed
                                            output_hidden_states=False, # Don't compute hidden states for speed
                                            return_dict_in_generate=False, # Return simple tensor for speed
                                            synced_gpus=False,     # Disable GPU synchronization for speed
                                        )

                            inference_time = (time.time() - inference_start) * 1000

                            # Check if we exceeded target time and log warning
                            if inference_time > 100:  # Target is 100ms
                                realtime_logger.warning(f"⚠️ Inference for chunk {chunk_id} took {inference_time:.1f}ms (target: 100ms)")
                            else:
                                realtime_logger.debug(f"⚡ Inference completed for chunk {chunk_id} in {inference_time:.1f}ms")

                        except Exception as e:
                            inference_time = (time.time() - inference_start) * 1000
                            realtime_logger.error(f"❌ Inference failed for chunk {chunk_id} after {inference_time:.1f}ms: {e}")
                            raise
                        
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
        """Unified conversational processing (legacy method)"""
        result = await self.process_realtime_chunk(
            audio_data,
            chunk_id=int(time.time() * 1000),
            mode="conversation"
        )
        return result['response']

    async def understand_audio(self, audio_data: torch.Tensor, question: str = "") -> str:
        """Unified conversational processing (legacy method)"""
        result = await self.process_realtime_chunk(
            audio_data,
            chunk_id=int(time.time() * 1000),
            mode="conversation"
        )
        return result['response']

    async def process_audio_stream(self, audio_data: torch.Tensor, prompt: str = "") -> str:
        """Unified conversational processing (legacy method)"""
        result = await self.process_realtime_chunk(
            audio_data,
            chunk_id=int(time.time() * 1000),
            mode="conversation"
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

    async def process_chunked_streaming(self, audio_data: np.ndarray, prompt: str = None,
                                      chunk_id: str = None, mode: str = "chunked_streaming"):
        """
        ULTRA-LOW LATENCY TOKEN-BY-TOKEN STREAMING
        Target: <100ms first token, <50ms inter-token intervals
        """
        if not self.is_initialized:
            raise RuntimeError("Voxtral model not initialized")

        chunk_id = chunk_id or f"stream_{int(time.time() * 1000000)}"
        pipeline_start = time.time()

        try:
            realtime_logger.info(f"🚀 Starting ultra-low latency streaming for {chunk_id}")

            # STEP 1: ULTRA-FAST AUDIO PREPROCESSING (Target: <5ms)
            preprocessing_start = time.time()

            # Direct tensor processing - no file I/O
            if hasattr(audio_data, 'cpu'):
                audio_tensor = audio_data.cpu() if audio_data.dtype == torch.float32 else audio_data.cpu().float()
            else:
                audio_tensor = torch.from_numpy(audio_data.astype(np.float32))

            # Fast normalization
            max_val = torch.abs(audio_tensor).max()
            if max_val > 0:
                audio_tensor = audio_tensor * (0.95 / max_val)

            preprocessing_time = (time.time() - preprocessing_start) * 1000
            realtime_logger.debug(f"⚡ Audio preprocessing: {preprocessing_time:.1f}ms")

            # STEP 2: ULTRA-FAST PROCESSOR (Target: <20ms)
            processor_start = time.time()

            # Create conversation format for audio processing
            import tempfile
            import soundfile as sf

            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp_file:
                sf.write(tmp_file.name, audio_tensor.numpy(), 16000)

                # Create conversation format
                conversation = [
                    {
                        "role": "user",
                        "content": [
                            {"type": "audio", "path": tmp_file.name}
                        ]
                    }
                ]

                # Process using apply_chat_template
                inputs = self.processor.apply_chat_template(conversation, return_tensors="pt")

                # Clean up temporary file
                import os
                os.unlink(tmp_file.name)

            # Move to device immediately
            if hasattr(inputs, 'to'):
                inputs = inputs.to(self.device)
            elif isinstance(inputs, dict):
                inputs = {k: v.to(self.device) if hasattr(v, 'to') else v for k, v in inputs.items()}

            processor_time = (time.time() - processor_start) * 1000
            realtime_logger.debug(f"⚡ Processor: {processor_time:.1f}ms")

            # STEP 3: TRUE TOKEN-BY-TOKEN STREAMING (Target: <100ms first token)
            inference_start = time.time()

            # Import streaming components
            from transformers import TextIteratorStreamer
            from threading import Thread
            import asyncio

            # Initialize streaming components
            tokenizer = self.processor.tokenizer if hasattr(self.processor, 'tokenizer') else None
            if not tokenizer:
                raise RuntimeError("Tokenizer not available for streaming")

            # Create ultra-fast streamer
            streamer = TextIteratorStreamer(
                tokenizer,
                timeout=10.0,  # Longer timeout to avoid queue.Empty errors
                skip_prompt=True,
                skip_special_tokens=True
            )

            # ULTRA-FAST generation parameters
            generation_kwargs = {
                **inputs,
                'streamer': streamer,
                'max_new_tokens': 150,
                'min_new_tokens': 1,
                'do_sample': False,  # Greedy for speed
                'num_beams': 1,
                'use_cache': True,
                'temperature': 0.0,
                'top_p': 1.0,
                'top_k': 1,
                'repetition_penalty': 1.0,
                'pad_token_id': tokenizer.eos_token_id,
                'eos_token_id': tokenizer.eos_token_id,
            }

            realtime_logger.info("🚀 Starting token-by-token streaming generation...")

            # Start generation in background thread for true streaming
            generation_thread = Thread(
                target=self.model.generate,
                kwargs=generation_kwargs
            )
            generation_thread.start()

            # STEP 4: REAL-TIME TOKEN STREAMING LOOP
            from src.utils.semantic_chunking import SemanticChunker

            # Initialize semantic chunker for word-level TTS triggering
            semantic_chunker = SemanticChunker()

            first_token_time = None
            first_word_time = None
            token_count = 0
            word_count = 0
            word_buffer = ""
            full_response = ""

            # Stream tokens as they become available
            last_token_time = inference_start
            try:
                for token_text in streamer:
                    current_time = time.time()
                    token_count += 1

                    # Calculate inter-token latency
                    inter_token_latency = (current_time - last_token_time) * 1000
                    last_token_time = current_time

                    # Track first token timing
                    if first_token_time is None:
                        first_token_time = current_time
                        first_token_latency = (first_token_time - inference_start) * 1000
                        realtime_logger.info(f"⚡ FIRST TOKEN: {first_token_latency:.1f}ms - '{token_text}'")
                    else:
                        # Log inter-token latency for verification
                        realtime_logger.debug(f"🔄 Token {token_count}: '{token_text}' (inter-token: {inter_token_latency:.1f}ms)")

                    # Yield immediate token for ultra-low latency
                    yield {
                        'type': 'token_chunk',
                        'text': token_text,
                        'tokens': [token_text],
                        'word_count': 0,
                        'boundary_type': 'token',
                        'confidence': 1.0,
                        'chunk_id': f"{chunk_id}_token_{token_count}",
                        'generation_time_ms': (current_time - inference_start) * 1000,
                        'inter_token_latency_ms': inter_token_latency,
                        'is_complete': False,
                        'timestamp': current_time,
                        'chunk_sequence': token_count
                    }

                    # Process for word boundaries using semantic chunker
                    semantic_chunk = semantic_chunker.process_token(token_text, current_time)
                    if semantic_chunk:
                        word_count += 1

                        if first_word_time is None:
                            first_word_time = current_time
                            first_word_latency = (first_word_time - inference_start) * 1000
                            realtime_logger.info(f"📝 FIRST WORD: {first_word_latency:.1f}ms - '{semantic_chunk.text}'")
                        else:
                            realtime_logger.debug(f"📝 Word {word_count}: '{semantic_chunk.text}' ({semantic_chunk.boundary_type.value})")

                        # Yield semantic chunk for immediate TTS processing
                        yield {
                            'type': 'semantic_chunk',
                            'text': semantic_chunk.text,
                            'tokens': semantic_chunk.tokens,
                            'word_count': semantic_chunk.word_count,
                            'boundary_type': semantic_chunk.boundary_type.value,
                            'confidence': semantic_chunk.confidence,
                            'chunk_id': f"{chunk_id}_{semantic_chunk.chunk_id}",
                            'generation_time_ms': (current_time - inference_start) * 1000,
                            'is_complete': False,
                            'timestamp': current_time,
                            'chunk_sequence': word_count
                        }

                        # Reset word buffer
                        word_buffer = ""

                    # Small yield to allow other coroutines to run
                    await asyncio.sleep(0.001)

                # Wait for generation thread to complete
                generation_thread.join(timeout=1.0)

            except Exception as streaming_error:
                realtime_logger.error(f"❌ Token streaming error: {streaming_error}")
                import traceback
                traceback.print_exc()

            # Finalize any remaining content in the chunker
            final_chunk = semantic_chunker.finalize_chunk(time.time())
            if final_chunk:
                yield {
                    'type': 'semantic_chunk',
                    'text': final_chunk.text,
                    'tokens': final_chunk.tokens,
                    'word_count': final_chunk.word_count,
                    'boundary_type': final_chunk.boundary_type.value,
                    'confidence': final_chunk.confidence,
                    'chunk_id': f"{chunk_id}_{final_chunk.chunk_id}",
                    'generation_time_ms': 0,
                    'is_complete': False,
                    'timestamp': final_chunk.timestamp
                }

            # Final completion
            total_time = (time.time() - pipeline_start) * 1000
            inference_time = (time.time() - inference_start) * 1000
            first_token_latency_ms = (first_token_time - inference_start) * 1000 if first_token_time else 0
            first_word_latency_ms = (first_word_time - inference_start) * 1000 if first_word_time else 0

            yield {
                'type': 'chunked_streaming_complete',
                'response_text': full_response,
                'total_tokens': token_count,
                'total_chunks': word_count,
                'first_token_latency_ms': first_token_latency_ms,
                'first_word_latency_ms': first_word_latency_ms,
                'inference_time_ms': inference_time,
                'total_time_ms': total_time,
                'chunk_id': chunk_id,
                'timestamp': time.time(),
                'streaming_method': 'TokenByTokenStreaming'
            }

            realtime_logger.info(f"✅ Token-by-token streaming completed for {chunk_id}")
            realtime_logger.info(f"   ⚡ First token: {first_token_latency_ms:.1f}ms")
            realtime_logger.info(f"   📝 First word: {first_word_latency_ms:.1f}ms")
            realtime_logger.info(f"   🔤 Total tokens: {token_count}")
            realtime_logger.info(f"   📊 Total time: {total_time:.1f}ms")

        except Exception as e:
            realtime_logger.error(f"❌ Error in chunked streaming for {chunk_id}: {e}")
            import traceback
            realtime_logger.error(f"❌ Full traceback: {traceback.format_exc()}")
            yield {
                'type': 'error',
                'error': str(e),
                'chunk_id': chunk_id,
                'is_complete': True,
                'timestamp': time.time()
            }

    async def process_streaming_chunk(self, audio_data: np.ndarray, prompt: str = None,
                                    chunk_id: str = None, mode: str = "streaming"):
        """
        STREAMING VOICE AGENT: Process audio with token-by-token streaming generation
        Yields tokens as they are generated for immediate TTS processing
        """
        if not self.is_initialized:
            raise RuntimeError("Voxtral model not initialized")

        chunk_id = chunk_id or f"stream_{int(time.time() * 1000)}"
        start_time = time.time()

        try:
            realtime_logger.info(f"🎙️ Starting streaming processing for chunk {chunk_id}")

            # Audio preprocessing (same as regular processing)
            preprocessing_start = time.time()

            # Ensure audio is in the correct format
            if audio_data.dtype != np.float32:
                audio_data = audio_data.astype(np.float32)

            # Normalize audio
            if np.max(np.abs(audio_data)) > 0:
                audio_data = audio_data / np.max(np.abs(audio_data)) * 0.95

            preprocessing_time = (time.time() - preprocessing_start) * 1000
            realtime_logger.debug(f"⚡ Audio preprocessing completed in {preprocessing_time:.1f}ms")

            # Process with VoxtralProcessor
            processor_start = time.time()

            # Use the processor to handle audio input
            if hasattr(self.processor, 'process_audio'):
                inputs = self.processor.process_audio(audio_data, sampling_rate=16000)
            else:
                # Fallback to direct processing
                inputs = self.processor(audio_data, sampling_rate=16000, return_tensors="pt")

            # Move inputs to device
            if isinstance(inputs, dict):
                inputs = {k: v.to(self.device) if hasattr(v, 'to') else v for k, v in inputs.items()}
            else:
                inputs = inputs.to(self.device)

            processor_time = (time.time() - processor_start) * 1000
            realtime_logger.debug(f"⚡ Processor completed in {processor_time:.1f}ms")

            # STREAMING INFERENCE: Generate tokens one by one
            inference_start = time.time()

            with torch.no_grad():
                with torch.autocast(device_type='cuda', dtype=torch.float16):
                    # STREAMING GENERATION: Iterative token generation for real-time streaming

                    # Initialize generation state
                    if isinstance(inputs, dict):
                        input_ids = inputs.get('input_ids', inputs.get('audio_values'))
                        attention_mask = inputs.get('attention_mask')
                    else:
                        input_ids = inputs
                        attention_mask = None

                    # Prepare for iterative generation
                    current_input_ids = input_ids
                    generated_tokens = []
                    word_buffer = ""
                    step = 0

                    # OPTIMIZED: Generation parameters for maximum speed
                    generation_config = {
                        'do_sample': False,     # OPTIMIZED: Greedy decoding for maximum speed
                        'temperature': 0.0,     # OPTIMIZED: No temperature for maximum speed
                        'top_p': 1.0,          # OPTIMIZED: No top-p filtering for speed
                        'top_k': 1,            # OPTIMIZED: Greedy selection only
                        'repetition_penalty': 1.0, # OPTIMIZED: No penalty overhead
                        'pad_token_id': self.processor.tokenizer.eos_token_id if hasattr(self.processor, 'tokenizer') else None,
                        'eos_token_id': self.processor.tokenizer.eos_token_id if hasattr(self.processor, 'tokenizer') else None,
                        'use_cache': True,
                    }

                    # STREAMING LOOP: Generate tokens one by one
                    for step in range(250):  # Max 250 tokens
                        # Generate next token
                        with torch.no_grad():
                            outputs = self.model.generate(
                                current_input_ids,
                                max_new_tokens=1,
                                min_new_tokens=1,
                                **generation_config,
                                output_scores=False,
                                output_attentions=False,
                                output_hidden_states=False,
                                return_dict_in_generate=False,
                                synced_gpus=False,
                            )

                        # Extract new token
                        new_token_id = outputs[0, -1].item()
                        generated_tokens.append(new_token_id)

                        # Decode token to text
                        if hasattr(self.processor, 'tokenizer'):
                            token_text = self.processor.tokenizer.decode([new_token_id], skip_special_tokens=True)
                        else:
                            token_text = f"<{new_token_id}>"

                        # Add to word buffer
                        word_buffer += token_text

                        # Check if we have complete words (2+ words for TTS trigger)
                        words = word_buffer.strip().split()
                        if len(words) >= 2 or any(char in token_text for char in ['.', '!', '?', '\n']):
                            # Send words for TTS processing
                            words_to_send = ' '.join(words[:-1]) if len(words) > 2 else ' '.join(words)
                            if words_to_send.strip():
                                yield {
                                    'type': 'words',
                                    'text': words_to_send.strip(),
                                    'tokens': generated_tokens[-len(words_to_send.split()):],
                                    'step': step,
                                    'is_complete': False,
                                    'chunk_id': chunk_id,
                                    'timestamp': time.time()
                                }

                                # Keep last word in buffer for next iteration
                                word_buffer = words[-1] if len(words) > 2 else ""

                        # Update input for next iteration
                        current_input_ids = outputs

                        # Check for EOS token
                        if new_token_id == generation_config.get('eos_token_id'):
                            break

                        # OPTIMIZED: Minimal delay for maximum speed
                        await asyncio.sleep(0.0001)

                    # Send any remaining text
                    if word_buffer.strip():
                        yield {
                            'type': 'words',
                            'text': word_buffer.strip(),
                            'tokens': [generated_tokens[-1]] if generated_tokens else [],
                            'step': step,
                            'is_complete': False,
                            'chunk_id': chunk_id,
                            'timestamp': time.time()
                        }

            inference_time = (time.time() - inference_start) * 1000
            total_time = (time.time() - start_time) * 1000

            # Decode final output
            if hasattr(self.processor, 'tokenizer'):
                response_text = self.processor.tokenizer.decode(outputs[0], skip_special_tokens=True)
            else:
                response_text = str(outputs[0])

            # Send completion marker
            yield {
                'type': 'complete',
                'chunk_id': chunk_id,
                'response_text': response_text,
                'inference_time_ms': inference_time,
                'total_time_ms': total_time,
                'is_complete': True,
                'timestamp': time.time()
            }

            realtime_logger.info(f"✅ Streaming generation completed for chunk {chunk_id} in {inference_time:.1f}ms")

        except Exception as e:
            realtime_logger.error(f"❌ Error in streaming processing for chunk {chunk_id}: {e}")
            yield {
                'type': 'error',
                'error': str(e),
                'chunk_id': chunk_id,
                'is_complete': True,
                'timestamp': time.time()
            }

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
