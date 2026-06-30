# Embedded defaults for Process Tower scan addresses.
# Keep this as the source used by packaged executables.

SCAN_ADDRESSES = [
    {
        "name": "SLDetection",
        "type": "pointer",
        "module": "UnityPlayer.dll",
        "base_offset": "0x01D1C1F0",
        "offsets": [
            "0x160",
            "0x80",
            "0x1E8",
            "0x1A8",
            "0x38",
            "0x98",
            "0x24",
        ],
        "description": "Number of characters visible on screen",
    },
    {
        "name": "MapOverlay",
        "type": "pointer",
        "module": "UnityPlayer.dll",
        "base_offset": "0x01D1BF08",
        "offsets": [
            "0x8",
            "0x18",
            "0xD0",
            "0x18",
            "0x38",
            "0x110",
            "0xA0",
        ],
        "description": "Map overlay settings",
    },
]
