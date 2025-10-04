"""
Speech-to-Speech Pipeline Coordinator
Unified pipeline: Audio Input → Voxtral (STT) → LLM Processing → Kokoro TTS → Audio Output
"""
import asyncio
import time
import logging
import numpy as np
import torch
from typing import Dict, Any, Optional, AsyncGenerator, Tuple
from threading import Lock
from collections import deque

from src.models.voxtral_model_realtime import voxtral_model
from src.models.kokoro_model_realtime import kokoro_model
from src.models.audio_processor_realtime import AudioProcessor
from src.utils.config import config

# Setup logging
pipeline_logger = logging.getLogger("speech_to_speech")

class SpeechToSpeechPipeline:
    """
    Production-ready Speech-to-Speech Pipeline
    Coordinates Voxtral STT and Kokoro TTS for complete conversational AI
    """
    
    def __init__(self):
        self.pipeline_lock = Lock()
        self.is_initialized = False
        
        # Pipeline components
        self.audio_processor = AudioProcessor()
        
        # ULTRA-LOW LATENCY: Reduced performance tracking overhead
        self.pipeline_history = deque(maxlen=20)  # Reduced from 50 for less memory overhead
        self.total_conversations = 0

        # ULTRA-LOW LATENCY: Pipeline configuration
        self.latency_target_ms = config.speech_to_speech.latency_target_ms
        self.enable_emotional_tts = False  # DISABLED: Skip emotional analysis for maximum speed

        # ULTRA-LOW LATENCY: Simplified conversation context
        self.conversation_context = deque(maxlen=5)  # Reduced from 10 for faster processing
        
        pipeline_logger.info(f"🔄 SpeechToSpeechPipeline initialized")
        pipeline_logger.info(f"   🎯 Target latency: {self.latency_target_ms}ms")
        pipeline_logger.info(f"   🎭 Emotional TTS: {self.enable_emotional_tts}")
    
    async def initialize(self):
        """Initialize all pipeline components"""
        if self.is_initialized:
            pipeline_logger.info("🔄 Speech-to-Speech pipeline already initialized")
            return
        
        start_time = time.time()
        pipeline_logger.info("🚀 Initializing Speech-to-Speech pipeline...")
        
        try:
            # Initialize Voxtral model (STT)
            if not voxtral_model.is_initialized:
                pipeline_logger.info("📥 Initializing Voxtral STT model...")
                await voxtral_model.initialize()
            
            # Initialize Kokoro model (TTS)
            if not kokoro_model.is_initialized:
                pipeline_logger.info("🎵 Initializing Kokoro TTS model...")
                await kokoro_model.initialize()
            
            self.is_initialized = True
            init_time = time.time() - start_time
            pipeline_logger.info(f"🎉 Speech-to-Speech pipeline fully initialized in {init_time:.2f}s!")
            pipeline_logger.info("🗣️ Ready for conversational AI interactions")
            
        except Exception as e:
            pipeline_logger.error(f"❌ Failed to initialize Speech-to-Speech pipeline: {e}")
            raise
    
    async def process_conversation_turn_chunked_streaming(self, audio_data: np.ndarray,
                                                        conversation_id: str = None,
                                                        voice_preference: str = None,
                                                        speed_preference: float = None) -> AsyncGenerator[Dict[str, Any], None]:
        """
        Process a complete conversation turn with CHUNKED STREAMING for sub-500ms latency
        Speech Input → Chunked Text Generation → Immediate TTS → Streaming Audio Output

        Args:
            audio_data: Input audio data from user
            conversation_id: Unique conversation identifier
            voice_preference: Preferred TTS voice
            speed_preference: Preferred TTS speed

        Yields:
            Dict containing streaming chunks with audio data, text, and metadata
        """
        if not self.is_initialized:
            raise RuntimeError("Pipeline not initialized. Call initialize() first.")

        turn_start_time = time.time()
        conversation_id = conversation_id or f"conv_chunked_{int(time.time() * 1000)}"
        self.total_conversations += 1

        pipeline_logger.info(f"🚀 Processing CHUNKED STREAMING conversation turn {conversation_id}")

        try:
            # Stage 1: Audio preprocessing and validation
            preprocessing_start = time.time()

            if not self.audio_processor.validate_realtime_chunk(audio_data, chunk_id=conversation_id):
                # Early return for silence
                yield {
                    'type': 'complete',
                    'conversation_id': conversation_id,
                    'transcription': '',
                    'response_text': '',
                    'total_latency_ms': (time.time() - turn_start_time) * 1000,
                    'success': True,
                    'is_silence': True
                }
                return

            # Preprocess audio
            audio_tensor = self.audio_processor.preprocess_realtime_chunk(
                audio_data,
                chunk_id=conversation_id
            )

            preprocessing_time = (time.time() - preprocessing_start) * 1000
            pipeline_logger.debug(f"⚡ Audio preprocessing: {preprocessing_time:.1f}ms")

            # Stage 2: CHUNKED STREAMING Speech-to-Text and Response Generation
            stt_start_time = time.time()
            first_chunk_time = None
            first_audio_time = None
            transcription_parts = []
            response_parts = []
            chunk_count = 0

            # Process with Voxtral chunked streaming
            async for chunk_data in voxtral_model.process_chunked_streaming(
                audio_tensor,
                prompt=None,
                chunk_id=conversation_id,
                mode="chunked_streaming"
            ):
                if chunk_data.get('type') == 'semantic_chunk':
                    chunk_text = chunk_data.get('text', '').strip()

                    if chunk_text:
                        if first_chunk_time is None:
                            first_chunk_time = time.time()
                            first_chunk_latency = (first_chunk_time - turn_start_time) * 1000
                            pipeline_logger.info(f"⚡ First semantic chunk ready in {first_chunk_latency:.1f}ms: '{chunk_text}'")

                        response_parts.append(chunk_text)
                        chunk_count += 1

                        # Yield text chunk immediately
                        yield {
                            'type': 'text_chunk',
                            'conversation_id': conversation_id,
                            'text': chunk_text,
                            'chunk_sequence': chunk_count,
                            'boundary_type': chunk_data.get('boundary_type', 'unknown'),
                            'confidence': chunk_data.get('confidence', 0.0),
                            'generation_time_ms': chunk_data.get('generation_time_ms', 0),
                            'timestamp': time.time()
                        }

                        # Stage 3: IMMEDIATE TTS Processing for this chunk
                        tts_start_time = time.time()
                        voice = voice_preference or 'af_heart'

                        try:
                            # Generate TTS for this semantic chunk immediately
                            async for tts_chunk in kokoro_model.synthesize_speech_streaming(
                                chunk_text,
                                voice=voice,
                                chunk_id=f"{conversation_id}_chunk_{chunk_count}"
                            ):
                                if tts_chunk.get('audio_chunk') is not None:
                                    if first_audio_time is None:
                                        first_audio_time = time.time()
                                        first_audio_latency = (first_audio_time - turn_start_time) * 1000
                                        pipeline_logger.info(f"🎵 First audio chunk ready in {first_audio_latency:.1f}ms")

                                    # Yield audio chunk immediately for playback
                                    yield {
                                        'type': 'audio_chunk',
                                        'conversation_id': conversation_id,
                                        'audio_data': tts_chunk['audio_chunk'],
                                        'sample_rate': tts_chunk.get('sample_rate', 24000),
                                        'chunk_sequence': chunk_count,
                                        'text_source': chunk_text,
                                        'chunk_index': tts_chunk.get('chunk_index', 0),
                                        'is_final_audio': tts_chunk.get('is_final', False),
                                        'synthesis_time_ms': tts_chunk.get('synthesis_time_ms', 0),
                                        'timestamp': time.time()
                                    }

                                elif tts_chunk.get('is_final'):
                                    tts_time = (time.time() - tts_start_time) * 1000
                                    pipeline_logger.debug(f"✅ TTS chunk {chunk_count} completed in {tts_time:.1f}ms")

                        except Exception as tts_error:
                            pipeline_logger.error(f"❌ TTS error for chunk '{chunk_text}': {tts_error}")
                            yield {
                                'type': 'error',
                                'conversation_id': conversation_id,
                                'error': f"TTS error: {str(tts_error)}",
                                'chunk_sequence': chunk_count,
                                'text_source': chunk_text
                            }

                elif chunk_data.get('type') == 'complete':
                    # Voxtral generation complete
                    stt_time = (time.time() - stt_start_time) * 1000
                    full_response = ' '.join(response_parts)

                    pipeline_logger.info(f"✅ Chunked streaming completed: {chunk_count} chunks in {stt_time:.1f}ms")
                    pipeline_logger.info(f"📝 Full response: '{full_response}'")
                    break

                elif chunk_data.get('type') == 'error':
                    pipeline_logger.error(f"❌ Voxtral chunked streaming error: {chunk_data.get('error')}")
                    yield {
                        'type': 'error',
                        'conversation_id': conversation_id,
                        'error': f"Voxtral error: {chunk_data.get('error')}",
                        'stage': 'speech_to_text'
                    }
                    return

            # Final completion
            total_time = (time.time() - turn_start_time) * 1000
            full_response = ' '.join(response_parts)

            yield {
                'type': 'complete',
                'conversation_id': conversation_id,
                'transcription': full_response,  # In this case, response is the transcription
                'response_text': full_response,
                'total_chunks': chunk_count,
                'total_latency_ms': total_time,
                'first_chunk_latency_ms': (first_chunk_time - turn_start_time) * 1000 if first_chunk_time else 0,
                'first_audio_latency_ms': (first_audio_time - turn_start_time) * 1000 if first_audio_time else 0,
                'success': True,
                'stage_timings': {
                    'preprocessing_ms': preprocessing_time,
                    'total_stt_ms': stt_time if 'stt_time' in locals() else 0,
                    'total_pipeline_ms': total_time
                }
            }

            # Update pipeline history
            self.pipeline_history.append({
                'conversation_id': conversation_id,
                'total_latency_ms': total_time,
                'first_chunk_latency_ms': (first_chunk_time - turn_start_time) * 1000 if first_chunk_time else 0,
                'first_audio_latency_ms': (first_audio_time - turn_start_time) * 1000 if first_audio_time else 0,
                'chunks_generated': chunk_count,
                'success': True,
                'timestamp': time.time()
            })

        except Exception as e:
            pipeline_logger.error(f"❌ Chunked streaming pipeline error for {conversation_id}: {e}")
            import traceback
            pipeline_logger.error(f"❌ Full traceback: {traceback.format_exc()}")

            yield {
                'type': 'error',
                'conversation_id': conversation_id,
                'error': str(e),
                'total_latency_ms': (time.time() - turn_start_time) * 1000,
                'success': False
            }

    async def process_conversation_turn(self, audio_data: np.ndarray,
                                      conversation_id: str = None,
                                      voice_preference: str = None,
                                      speed_preference: float = None) -> Dict[str, Any]:
        """
        Process a complete conversation turn: Speech Input → Text → Response → Speech Output
        
        Args:
            audio_data: Input audio data from user
            conversation_id: Unique conversation identifier
            voice_preference: Preferred TTS voice
            speed_preference: Preferred TTS speed
            
        Returns:
            Dict containing transcription, response text, response audio, and metrics
        """
        if not self.is_initialized:
            raise RuntimeError("Pipeline not initialized. Call initialize() first.")
        
        turn_start_time = time.time()
        conversation_id = conversation_id or f"conv_{int(time.time() * 1000)}"
        self.total_conversations += 1
        
        pipeline_logger.info(f"🗣️ Processing conversation turn {conversation_id}")
        
        try:
            # ULTRA-LOW LATENCY: Stage 1 - Speech-to-Text (Voxtral) with optimized preprocessing
            stt_start_time = time.time()

            # OPTIMIZATION: Combined VAD validation and preprocessing in single step
            if not self.audio_processor.validate_realtime_chunk(audio_data, chunk_id=conversation_id):
                # Early return for silence - minimal processing
                return {
                    'conversation_id': conversation_id,
                    'transcription': '',
                    'response_text': '',
                    'response_audio': np.array([]),
                    'sample_rate': kokoro_model.sample_rate,
                    'total_latency_ms': (time.time() - turn_start_time) * 1000,
                    'success': True,
                    'is_silence': True
                }

            # OPTIMIZATION: Only preprocess if VAD passed (avoid unnecessary work)
            audio_tensor = self.audio_processor.preprocess_realtime_chunk(
                audio_data,
                chunk_id=conversation_id
            )
            
            # Process with Voxtral
            stt_result = await voxtral_model.process_realtime_chunk(
                audio_tensor,
                chunk_id=conversation_id,
                mode="conversation"
            )
            
            stt_time = (time.time() - stt_start_time) * 1000
            
            if not stt_result['success'] or stt_result.get('is_silence', False):
                pipeline_logger.debug(f"🔇 Conversation turn {conversation_id}: STT returned silence")
                return {
                    'conversation_id': conversation_id,
                    'transcription': stt_result.get('response', ''),
                    'response_text': '',
                    'response_audio': np.array([]),
                    'sample_rate': kokoro_model.sample_rate,
                    'total_latency_ms': (time.time() - turn_start_time) * 1000,
                    'success': True,
                    'is_silence': True,
                    'stage_timings': {
                        'stt_ms': stt_time
                    }
                }
            
            transcription = stt_result['response']
            pipeline_logger.info(f"📝 Transcription: '{transcription}'")
            
            # Stage 2: Generate Response (LLM Processing) - OPTIMIZED
            llm_start_time = time.time()
            response_text = await self._generate_response(transcription, conversation_id)
            llm_time = (time.time() - llm_start_time) * 1000

            pipeline_logger.info(f"💭 Response: '{response_text}'")

            # ULTRA-LOW LATENCY: Skip TTS for very short responses
            if len(response_text.strip()) < 2:  # Even more aggressive threshold
                return {
                    'conversation_id': conversation_id,
                    'transcription': transcription,
                    'response_text': response_text,
                    'response_audio': np.array([]),
                    'sample_rate': kokoro_model.sample_rate,
                    'total_latency_ms': (time.time() - turn_start_time) * 1000,
                    'success': True,
                    'tts_skipped': True
                }

            # ULTRA-LOW LATENCY: Stage 3 - Text-to-Speech (Kokoro) with minimal overhead
            tts_start_time = time.time()

            # ULTRA-OPTIMIZATION: Use fixed voice/speed for maximum speed (skip analysis)
            voice = voice_preference or 'af_heart'  # Fixed default voice for speed
            speed = speed_preference or 1.0         # Fixed default speed for speed

            # ULTRA-OPTIMIZATION: Reduced timeout for faster failure detection
            try:
                tts_result = await asyncio.wait_for(
                    kokoro_model.synthesize_speech(
                        response_text,
                        voice=voice,
                        speed=speed,
                        chunk_id=conversation_id
                    ),
                    timeout=3.0  # REDUCED: 3 second timeout for ultra-low latency
                )
            except asyncio.TimeoutError:
                pipeline_logger.error(f"❌ TTS timeout for conversation {conversation_id}")
                tts_result = {'success': False, 'error': 'TTS timeout'}

            tts_time = (time.time() - tts_start_time) * 1000
            
            if not tts_result['success']:
                pipeline_logger.error(f"❌ TTS failed for conversation {conversation_id}: {tts_result.get('error', 'Unknown error')}")
                # Return text-only response as fallback
                return {
                    'conversation_id': conversation_id,
                    'transcription': transcription,
                    'response_text': response_text,
                    'response_audio': np.array([]),
                    'sample_rate': kokoro_model.sample_rate,
                    'total_latency_ms': (time.time() - turn_start_time) * 1000,
                    'success': True,
                    'tts_failed': True,
                    'tts_error': tts_result.get('error', 'Unknown TTS error'),
                    'stage_timings': {
                        'stt_ms': stt_time,
                        'llm_ms': llm_time,
                        'tts_ms': tts_time
                    }
                }
            
            # Calculate total latency
            total_latency = (time.time() - turn_start_time) * 1000
            
            # Analyze emotional context
            emotion_analysis = self.analyze_conversation_emotion(transcription, response_text)

            # Update conversation context with emotional information
            self.conversation_context.append({
                'user_input': transcription,
                'ai_response': response_text,
                'timestamp': time.time(),
                'emotion_analysis': emotion_analysis
            })

            pipeline_logger.info(f"🎭 Emotional analysis: User={emotion_analysis['user_emotion']}, "
                                f"AI={emotion_analysis['response_emotion']}, "
                                f"Appropriateness={emotion_analysis['appropriateness_score']:.1f}, "
                                f"Voice={emotion_analysis['voice_selected']}")
            pipeline_logger.debug(f"🧠 Emotional reasoning: {emotion_analysis['emotional_reasoning']}")
            
            # Track performance metrics
            performance_stats = {
                'total_latency_ms': total_latency,
                'stt_time_ms': stt_time,
                'llm_time_ms': llm_time,
                'tts_time_ms': tts_time,
                'audio_duration_s': tts_result['audio_duration_s'],
                'meets_target': total_latency <= self.latency_target_ms
            }
            
            self.pipeline_history.append(performance_stats)
            
            pipeline_logger.info(f"✅ Conversation turn {conversation_id} completed in {total_latency:.1f}ms "
                                f"(Target: {self.latency_target_ms}ms, {'✅' if performance_stats['meets_target'] else '⚠️'})")
            
            return {
                'conversation_id': conversation_id,
                'transcription': transcription,
                'response_text': response_text,
                'response_audio': tts_result['audio_data'],
                'sample_rate': tts_result['sample_rate'],
                'total_latency_ms': total_latency,
                'success': True,
                'is_silence': False,
                'voice_used': tts_result['voice_used'],
                'speed_used': tts_result['speed_used'],
                'emotion_analysis': emotion_analysis,
                'stage_timings': {
                    'stt_ms': stt_time,
                    'llm_ms': llm_time,
                    'tts_ms': tts_time
                },
                'performance_stats': performance_stats
            }
            
        except Exception as e:
            total_latency = (time.time() - turn_start_time) * 1000
            pipeline_logger.error(f"❌ Error in conversation turn {conversation_id}: {e}")
            
            return {
                'conversation_id': conversation_id,
                'transcription': '',
                'response_text': '',
                'response_audio': np.array([]),
                'sample_rate': kokoro_model.sample_rate,
                'total_latency_ms': total_latency,
                'success': False,
                'error': str(e),
                'is_silence': False
            }
    
    async def _generate_response(self, user_input: str, conversation_id: str) -> str:
        """
        Generate AI response to user input
        This is a simple implementation - can be enhanced with proper LLM integration
        """
        # Simple response generation for now
        # In production, this would integrate with a proper LLM
        
        user_input_lower = user_input.lower()
        
        # Simple conversational responses
        if any(greeting in user_input_lower for greeting in ['hello', 'hi', 'hey']):
            return "Hello! How can I help you today?"
        elif any(question in user_input_lower for question in ['how are you', 'how do you do']):
            return "I'm doing well, thank you for asking! How are you?"
        elif any(thanks in user_input_lower for thanks in ['thank you', 'thanks']):
            return "You're very welcome! Is there anything else I can help you with?"
        elif any(goodbye in user_input_lower for goodbye in ['goodbye', 'bye', 'see you']):
            return "Goodbye! It was nice talking with you. Have a great day!"
        elif 'weather' in user_input_lower:
            return "I don't have access to current weather data, but I'd recommend checking a weather app or website for the most accurate information."
        elif 'time' in user_input_lower:
            return "I don't have access to the current time, but you can check your device's clock or ask your system for the time."
        else:
            # Generic helpful response
            return f"I understand you said: '{user_input}'. That's interesting! Could you tell me more about what you'd like to know or discuss?"
    
    def _select_voice_for_response(self, response_text: str) -> str:
        """Select appropriate voice based on response content and emotional context"""
        if self.enable_emotional_tts:
            emotion = self._detect_emotion_in_text(response_text)
            return self._get_voice_for_emotion(emotion)

        # Default voice
        return kokoro_model.voice

    def _detect_emotion_in_text(self, text: str) -> str:
        """
        Advanced emotion detection based on text content and context
        Returns: 'happy', 'sad', 'excited', 'calm', 'professional', 'friendly', 'empathetic'
        """
        text_lower = text.lower()

        # Excitement and joy indicators
        excitement_words = ['excited', 'amazing', 'wonderful', 'fantastic', 'great', 'awesome',
                           'brilliant', 'excellent', 'perfect', 'love', 'thrilled', 'delighted']
        if any(word in text_lower for word in excitement_words):
            return 'excited'

        # Happiness and positivity indicators
        happy_words = ['happy', 'glad', 'pleased', 'cheerful', 'joyful', 'good news',
                      'congratulations', 'well done', 'success', 'achievement']
        if any(word in text_lower for word in happy_words):
            return 'happy'

        # Sadness and empathy indicators
        sad_words = ['sorry', 'unfortunately', 'sad', 'disappointed', 'regret', 'apologize',
                    'condolences', 'sympathy', 'difficult', 'challenging', 'problem']
        if any(word in text_lower for word in sad_words):
            return 'empathetic'

        # Professional and formal indicators
        professional_words = ['business', 'formal', 'official', 'professional', 'corporate',
                             'meeting', 'presentation', 'report', 'analysis', 'documentation']
        if any(word in text_lower for word in professional_words):
            return 'professional'

        # Calm and soothing indicators
        calm_words = ['calm', 'relax', 'peaceful', 'gentle', 'soothing', 'meditation',
                     'breathe', 'slowly', 'carefully', 'patience']
        if any(word in text_lower for word in calm_words):
            return 'calm'

        # Question marks and interactive content suggest friendly tone
        if '?' in text or any(word in text_lower for word in ['how', 'what', 'when', 'where', 'why']):
            return 'friendly'

        # Default to friendly for general conversation
        return 'friendly'

    def _get_voice_for_emotion(self, emotion: str) -> str:
        """Map emotions to appropriate Kokoro voices"""
        emotion_voice_map = {
            'excited': 'af_bella',      # Energetic female voice
            'happy': 'af_sky',          # Bright female voice
            'empathetic': 'af_sarah',   # Gentle, caring female voice
            'professional': 'af_nicole', # Professional female voice
            'calm': 'af_heart',         # Calm, soothing female voice
            'friendly': 'af_heart',     # Default friendly voice
        }

        return emotion_voice_map.get(emotion, kokoro_model.voice)
    
    def _select_speed_for_response(self, response_text: str) -> float:
        """Select appropriate speech speed based on response content and emotional context"""
        if self.enable_emotional_tts:
            emotion = self._detect_emotion_in_text(response_text)
            return self._get_speed_for_emotion(emotion, response_text)

        # Default speed
        return kokoro_model.speed

    def _get_speed_for_emotion(self, emotion: str, text: str) -> float:
        """Map emotions and content to appropriate speech speeds"""
        base_speed = kokoro_model.speed
        text_lower = text.lower()

        # Emotion-based speed adjustments
        if emotion == 'excited':
            speed_multiplier = 1.15  # Slightly faster for excitement
        elif emotion == 'calm':
            speed_multiplier = 0.9   # Slower for calm, soothing speech
        elif emotion == 'empathetic':
            speed_multiplier = 0.85  # Slower for empathetic responses
        elif emotion == 'professional':
            speed_multiplier = 1.0   # Normal speed for professional tone
        else:
            speed_multiplier = 1.0   # Default speed

        # Content-based adjustments
        if any(word in text_lower for word in ['urgent', 'quickly', 'fast', 'hurry', 'emergency']):
            speed_multiplier *= 1.2  # Faster for urgent content
        elif any(word in text_lower for word in ['slowly', 'carefully', 'step by step', 'detailed']):
            speed_multiplier *= 0.8  # Slower for detailed explanations
        elif len(text) > 200:  # Long responses
            speed_multiplier *= 0.95  # Slightly slower for long content

        # Apply speed multiplier and clamp to valid range
        final_speed = base_speed * speed_multiplier
        return max(0.5, min(2.0, final_speed))

    def analyze_conversation_emotion(self, user_input: str, ai_response: str) -> Dict[str, Any]:
        """
        Analyze the emotional context of the entire conversation turn
        Returns detailed emotion analysis for logging and optimization
        """
        user_emotion = self._detect_emotion_in_text(user_input) if user_input else 'neutral'
        response_emotion = self._detect_emotion_in_text(ai_response) if ai_response else 'neutral'

        # Determine if emotion matching is appropriate
        emotion_match = user_emotion == response_emotion

        # Calculate emotional appropriateness score
        appropriateness_score = self._calculate_emotional_appropriateness(user_emotion, response_emotion)

        return {
            'user_emotion': user_emotion,
            'response_emotion': response_emotion,
            'emotion_match': emotion_match,
            'appropriateness_score': appropriateness_score,
            'voice_selected': self._get_voice_for_emotion(response_emotion),
            'speed_selected': self._get_speed_for_emotion(response_emotion, ai_response),
            'emotional_reasoning': self._get_emotional_reasoning(user_emotion, response_emotion)
        }

    def _calculate_emotional_appropriateness(self, user_emotion: str, response_emotion: str) -> float:
        """Calculate how appropriate the response emotion is given the user's emotion"""
        # Define appropriate response emotions for each user emotion
        appropriate_responses = {
            'excited': ['excited', 'happy', 'friendly'],
            'happy': ['happy', 'excited', 'friendly'],
            'sad': ['empathetic', 'calm', 'friendly'],
            'empathetic': ['empathetic', 'calm', 'friendly'],
            'professional': ['professional', 'friendly'],
            'calm': ['calm', 'friendly', 'professional'],
            'friendly': ['friendly', 'happy', 'calm']
        }

        if response_emotion in appropriate_responses.get(user_emotion, ['friendly']):
            return 1.0  # Perfect match
        elif response_emotion == 'friendly':
            return 0.8  # Friendly is generally appropriate
        else:
            return 0.6  # Acceptable but not optimal

    def _get_emotional_reasoning(self, user_emotion: str, response_emotion: str) -> str:
        """Provide reasoning for the emotional choice"""
        if user_emotion == response_emotion:
            return f"Matched user's {user_emotion} emotion for empathetic response"
        elif response_emotion == 'empathetic' and user_emotion in ['sad', 'disappointed']:
            return f"Used empathetic tone to respond to user's {user_emotion} emotion"
        elif response_emotion == 'professional' and user_emotion == 'professional':
            return "Maintained professional tone to match user's formal communication"
        elif response_emotion == 'calm' and user_emotion in ['excited', 'urgent']:
            return "Used calm tone to provide balanced response to user's energy"
        else:
            return f"Selected {response_emotion} tone as appropriate response to {user_emotion} input"
    
    def get_pipeline_info(self) -> Dict[str, Any]:
        """Get comprehensive pipeline information and performance statistics"""
        base_info = {
            "pipeline_type": "speech_to_speech",
            "is_initialized": self.is_initialized,
            "total_conversations": self.total_conversations,
            "latency_target_ms": self.latency_target_ms,
            "emotional_tts_enabled": self.enable_emotional_tts,
            "conversation_context_size": len(self.conversation_context)
        }
        
        if self.is_initialized:
            # Add component status
            base_info.update({
                "components": {
                    "voxtral_stt": voxtral_model.get_model_info(),
                    "kokoro_tts": kokoro_model.get_model_info()
                }
            })
            
            # Add performance statistics
            if self.pipeline_history:
                recent_stats = list(self.pipeline_history)[-10:]  # Last 10 conversations
                
                avg_latency = sum(s['total_latency_ms'] for s in recent_stats) / len(recent_stats)
                avg_stt_time = sum(s['stt_time_ms'] for s in recent_stats) / len(recent_stats)
                avg_llm_time = sum(s['llm_time_ms'] for s in recent_stats) / len(recent_stats)
                avg_tts_time = sum(s['tts_time_ms'] for s in recent_stats) / len(recent_stats)
                target_met_rate = sum(1 for s in recent_stats if s['meets_target']) / len(recent_stats) * 100
                
                base_info.update({
                    "performance_stats": {
                        "avg_total_latency_ms": round(avg_latency, 1),
                        "avg_stt_time_ms": round(avg_stt_time, 1),
                        "avg_llm_time_ms": round(avg_llm_time, 1),
                        "avg_tts_time_ms": round(avg_tts_time, 1),
                        "target_met_rate_percent": round(target_met_rate, 1),
                        "recent_conversations": len(recent_stats),
                        "history_size": len(self.pipeline_history)
                    }
                })
        
        return base_info

# Global pipeline instance
speech_to_speech_pipeline = SpeechToSpeechPipeline()

# Main execution block for testing
if __name__ == "__main__":
    import asyncio
    
    async def test_pipeline():
        """Test the complete speech-to-speech pipeline"""
        try:
            await speech_to_speech_pipeline.initialize()
            
            # Create test audio (simple sine wave)
            sample_rate = 16000
            duration = 2.0
            frequency = 440
            t = np.linspace(0, duration, int(sample_rate * duration))
            test_audio = np.sin(2 * np.pi * frequency * t).astype(np.float32) * 0.1
            
            result = await speech_to_speech_pipeline.process_conversation_turn(
                test_audio, 
                conversation_id="test_001"
            )
            
            if result['success']:
                print(f"✅ Pipeline test successful!")
                print(f"   Transcription: '{result['transcription']}'")
                print(f"   Response: '{result['response_text']}'")
                print(f"   Total latency: {result['total_latency_ms']:.1f}ms")
                
                if len(result['response_audio']) > 0:
                    import soundfile as sf
                    sf.write('test_pipeline_output.wav', result['response_audio'], result['sample_rate'])
                    print(f"   Response audio saved to test_pipeline_output.wav")
            else:
                print(f"❌ Pipeline test failed: {result.get('error', 'Unknown error')}")
                
        except Exception as e:
            print(f"❌ Pipeline test error: {e}")
    
    asyncio.run(test_pipeline())
