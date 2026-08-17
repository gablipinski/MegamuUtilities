# Embedded defaults for Outpost pointer addresses.
# Keep this as the source used by packaged executables.

SCAN_ADDRESSES = [
    {
        "name": "HP",
        "type": "pointer",
        "module": "GameAssembly.dll",
        "base_offset": "0x057DFE80",
        "offsets": [
            "0x70",
            "0xA70",
            "0x80",
            "0x40",
            "0x1E8",
            "0x30",
            "0x3C",
        ],
        "description": "Health pointer",
    },
    {
        "name": "SD",
        "type": "pointer",
        "module": "GameAssembly.dll",
        "base_offset": "0x054C6AA0",
        "offsets": [
            "0xD0",
            "0xB8",
            "0x0",
            "0x210",
            "0x1B8",
            "0x20",
            "0x4C",
        ],
        "description": "Stamina pointer",
    },
]
