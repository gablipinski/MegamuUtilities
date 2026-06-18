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
        "module": "GameAssembly.dll",
        "base_offset": "0x054276E8",
        "offsets": [
            "0xB8",
            "0x30",
            "0x30",
            "0xA8",
            "0x108",
            "0x10",
            "0x130",
        ],
        "description": "Map overlay settings",
    },
]
