import base64
from pathlib import Path
from urllib.request import urlopen


def read_audio_source(uri: str) -> bytes:
	if uri.startswith("data:"):
		_, encoded = uri.split(",", 1)
		return base64.b64decode(encoded)
	if uri.startswith(("http://", "https://")):
		with urlopen(uri, timeout=30) as response:
			return response.read()
	return Path(uri).read_bytes()
