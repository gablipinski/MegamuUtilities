import asyncio
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Optional
import mss
import easyocr
import numpy as np
from PIL import Image
from config import MonitorConfig, WindowConfig


@dataclass
class DetectionTrack:
    window_position: str
    char_name: str
    guild_name: Optional[str]
    x: float
    y: float
    notified_x: float
    notified_y: float
    best_confidence: float
    observations: int
    last_seen_at: float
    notified: bool = False

class ScreenMonitor:
    """Monitora janelas de jogo e detecta personagens"""
    
    def __init__(self, config: MonitorConfig):
        self.config = config
        self.reader = easyocr.Reader([config.ocr_language])
        self.active_tracks: list[DetectionTrack] = []
        self.known_guild_words: set[str] = set()  # inner words from bracket-confirmed guild tags
        self.is_first_scan = True
        self.is_running = False
        
    async def start_monitoring(self):
        """Inicia o monitoramento contínuo"""
        self.is_running = True
        print('[📺] Iniciando monitoramento de tela...\n')
        
        while self.is_running:
            try:
                await self.capture_and_analyze()
                await asyncio.sleep(self.config.capture_interval_ms / 1000)
            except Exception as e:
                print(f'[✗] Erro durante captura: {e}')
                await asyncio.sleep(1)
    
    async def capture_and_analyze(self):
        """Captura tela e analisa cada janela de jogo"""
        now = time.monotonic()
        self.cleanup_expired_tracks(now)

        with mss.mss() as sct:
            for window in self.config.windows:
                screenshot = sct.grab({
                    'left': window.x,
                    'top': window.y,
                    'width': window.width,
                    'height': window.height
                })
                
                # Converte para PIL Image
                img = Image.frombytes('RGB', screenshot.size, screenshot.rgb)

                # Recorta área útil para OCR (ignora chat lateral e HUD inferior)
                roi_img = self.crop_scan_region(img)
                
                # Detecta personagens
                detections = await self.detect_characters(roi_img, window)
                
                # Atualiza trilhas e notifica conforme regra de movimento.
                for detection in detections:
                    track = self.match_or_create_track(window, detection, now)

                    if track.notified:
                        # Re-alerta se o personagem se moveu desde a última notificação
                        dist = self.calculate_distance(
                            track.notified_x, track.notified_y, track.x, track.y
                        )
                        if dist > self.config.movement_retrigger_px:
                            track.notified_x = track.x
                            track.notified_y = track.y
                            await self.notify_character_found(track.char_name, window.map_name, track.guild_name)
                        continue

                    # No primeiro scan, notifica imediatamente; depois, espera N confirmações
                    if self.is_first_scan or track.observations >= self.config.min_observations_to_notify:
                        track.notified = True
                        track.notified_x = track.x
                        track.notified_y = track.y
                        await self.notify_character_found(track.char_name, window.map_name, track.guild_name)

        # Marca o primeiro ciclo como concluído após processar todas as janelas
        if self.is_first_scan:
            self.is_first_scan = False

    def cleanup_expired_tracks(self, now: float):
        """Remove entidades que sumiram por tempo suficiente para permitir novos alertas."""
        expiry_seconds = self.config.track_expiry_ms / 1000
        self.active_tracks = [
            track for track in self.active_tracks
            if (now - track.last_seen_at) <= expiry_seconds
        ]

    def calculate_distance(self, x1: float, y1: float, x2: float, y2: float) -> float:
        return ((x2 - x1) ** 2 + (y2 - y1) ** 2) ** 0.5

    def update_track_from_detection(self, track: DetectionTrack, detection: dict[str, float | str | None], now: float):
        """Atualiza a trilha mantendo o melhor nome OCR conhecido para a entidade."""
        track.x = float(detection['x'])
        track.y = float(detection['y'])
        track.last_seen_at = now
        track.observations += 1

        detection_confidence = float(detection['confidence'])
        if detection_confidence >= track.best_confidence:
            track.best_confidence = detection_confidence
            track.char_name = str(detection['char_name'])
            detection_guild = detection.get('guild_name')
            track.guild_name = str(detection_guild) if detection_guild else None

    def match_or_create_track(
        self,
        window: WindowConfig,
        detection: dict[str, float | str | None],
        now: float,
    ) -> DetectionTrack:
        """Relaciona uma leitura OCR com uma entidade já vista pela posição na tela."""
        detection_x = float(detection['x'])
        detection_y = float(detection['y'])

        best_track = None
        best_distance = float('inf')

        for track in self.active_tracks:
            if track.window_position != window.position:
                continue

            distance = self.calculate_distance(track.x, track.y, detection_x, detection_y)
            if distance <= self.config.track_match_distance_px and distance < best_distance:
                best_track = track
                best_distance = distance

        if best_track is not None:
            self.update_track_from_detection(best_track, detection, now)
            return best_track

        new_track = DetectionTrack(
            window_position=window.position,
            char_name=str(detection['char_name']),
            guild_name=str(detection['guild_name']) if detection.get('guild_name') else None,
            x=detection_x,
            y=detection_y,
            notified_x=detection_x,
            notified_y=detection_y,
            best_confidence=float(detection['confidence']),
            observations=1,
            last_seen_at=now,
        )
        self.active_tracks.append(new_track)
        return new_track

    def crop_scan_region(self, image: Image.Image) -> Image.Image:
        """Recorta somente a região provável de nomes de personagens."""
        width, height = image.size

        left_pct = float(self.config.scan_region.get('left_pct', 0.25))
        top_pct = float(self.config.scan_region.get('top_pct', 0.08))
        right_pct = float(self.config.scan_region.get('right_pct', 0.95))
        bottom_pct = float(self.config.scan_region.get('bottom_pct', 0.72))

        left = max(0, min(width - 1, int(width * left_pct)))
        top = max(0, min(height - 1, int(height * top_pct)))
        right = max(left + 1, min(width, int(width * right_pct)))
        bottom = max(top + 1, min(height, int(height * bottom_pct)))

        return image.crop((left, top, right, bottom))

    def is_valid_character_name(self, text: str) -> bool:
        """Filtra OCR para manter apenas candidatos plausíveis a nome de personagem."""
        if not text:
            return False

        name = text.strip()
        normalized_name = self.normalize_guild_text(name)

        if not (3 <= len(name) <= 16):
            return False

        # Nunca trate tags de guild como nome de personagem.
        if '<' in normalized_name or '>' in normalized_name:
            return False

        # Nomes de chat/eventos costumam incluir espaços e pontuação pesada.
        if ' ' in name:
            return False

        if any(ch in name for ch in '[]{}():;,.!?/@#$%*|\\'):
            return False

        if not any(ch.isalpha() for ch in name):
            return False

        # Nomes de personagem em MU Online têm capitalização mista.
        # Texto em ALL-CAPS é quase sempre um fragmento de tag de guild.
        alpha_chars = [ch for ch in name if ch.isalpha()]
        uppercase_ratio = sum(1 for ch in alpha_chars if ch.isupper()) / len(alpha_chars)
        if uppercase_ratio > 0.75:
            return False

        digit_count = sum(ch.isdigit() for ch in name)
        if digit_count > 4:
            return False

        return True

    def normalize_guild_text(self, text: str) -> str:
        """Normaliza o texto de guild para reduzir variações de OCR."""
        normalized = text.strip()
        normalized = normalized.replace('«', '<').replace('»', '>')
        normalized = normalized.replace('[', '<').replace(']', '>')
        return normalized

    def is_valid_guild_name(self, text: str) -> bool:
        """Detecta padrões de guild tag, preferindo texto entre < >."""
        if not text:
            return False

        guild_text = self.normalize_guild_text(text)
        if not (3 <= len(guild_text) <= 24):
            return False

        has_brackets = '<' in guild_text or '>' in guild_text
        if not has_brackets:
            return False

        inner = guild_text.replace('<', '').replace('>', '').strip()
        if not inner:
            return False

        # Multi-word guild tags like < ASGARDV - TRUETAG4 > are valid — allow spaces/dashes
        if not any(ch.isalpha() for ch in inner):
            return False

        if any(ch in inner for ch in '[]{}():;,.!?/@#$%*|\\'):
            return False

        return True

    def get_box_center(self, bbox) -> tuple[float, float]:
        """Retorna o centro (x, y) do bounding box da detecção OCR."""
        xs = [point[0] for point in bbox]
        ys = [point[1] for point in bbox]
        return (sum(xs) / 4.0, sum(ys) / 4.0)

    def pair_guilds_with_characters(
        self,
        characters: list[dict[str, float | str]],
        guilds: list[dict[str, float | str]]
    ) -> list[dict[str, float | str | None]]:
        """Relaciona guild detectada acima do nome do personagem (quando existir)."""
        combined = []

        for char_item in characters:
            char_x = float(char_item['x'])
            char_y = float(char_item['y'])

            best_guild = None
            best_score = float('inf')

            for guild_item in guilds:
                guild_x = float(guild_item['x'])
                guild_y = float(guild_item['y'])

                dy = char_y - guild_y
                if dy <= 0 or dy > 90:
                    continue

                dx = abs(char_x - guild_x)
                if dx > 140:
                    continue

                score = dx + (dy * 0.6)
                if score < best_score:
                    best_score = score
                    best_guild = str(guild_item['text'])

            combined.append({
                'char_name': str(char_item['text']),
                'guild_name': best_guild,
                'x': char_x,
                'y': char_y,
                'confidence': float(char_item['confidence']),
            })

        return combined
    
    async def detect_characters(self, image: Image.Image, window: WindowConfig) -> list[dict[str, float | str | None]]:
        """Usa OCR para detectar nome do personagem e guild (opcional)."""
        try:
            # Converte PIL Image para numpy array
            img_array = np.array(image)
            
            # Detecta texto com EasyOCR
            results = self.reader.readtext(img_array)
            
            characters = []
            guilds = []

            for detection in results:
                bbox = detection[0]
                text = detection[1]
                confidence = detection[2]
                
                # Filtra por confiança
                if confidence >= self.config.char_detection_threshold:
                    candidate = text.strip()

                    if self.is_valid_guild_name(candidate):
                        x, y = self.get_box_center(bbox)
                        normalized = self.normalize_guild_text(candidate)
                        # Strip any stray bracket chars before storing/displaying
                        clean_guild = normalized.replace('<', '').replace('>', '').strip()
                        guilds.append({'text': clean_guild, 'x': x, 'y': y})
                        # Extract individual words so that OCR fragments of this guild
                        # are rejected as character names in future frames.
                        for word in clean_guild.split():
                            token = ''.join(ch for ch in word if ch.isalnum())
                            if len(token) >= 3:
                                self.known_guild_words.add(token.upper())
                        continue

                    if self.is_valid_character_name(candidate):
                        # Reject if this token was previously seen inside a bracket-confirmed guild tag
                        if candidate.upper() in self.known_guild_words:
                            continue
                        x, y = self.get_box_center(bbox)
                        characters.append({
                            'text': candidate,
                            'x': x,
                            'y': y,
                            'confidence': float(confidence),
                        })
                        continue

            return self.pair_guilds_with_characters(characters, guilds)
        except Exception as e:
            print(f'[✗] Erro ao detectar personagens em {window.position}: {e}')
            return []
    
    async def notify_character_found(self, char_name: str, map_name: str, guild_name: Optional[str] = None):
        """Notifica quando um personagem é detectado"""
        timestamp = datetime.now().strftime('%H:%M:%S')
        print(f'[🎯] [{timestamp}] Personagem detectado!')
        if guild_name:
            print(f'    Guild: {guild_name}')
        print(f'    Nome: {char_name}')
        print(f'    Mapa: {map_name}\n')
        
        # Aqui a notificação WhatsApp será enviada
        # (será implementada em whatsapp_notifier.py)
    
    async def stop_monitoring(self):
        """Para o monitoramento"""
        print('\n[⏹️] Parando monitoramento...')
        self.is_running = False
        print('[✓] Monitoramento encerrado')
