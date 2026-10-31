"""Shared Forever race restrictions for the raid and LFG character manager."""

RACES = ("Orc", "Troll", "Tauren", "Undead", "Skyborne (Windshaper)")

STARTING_ZONES = {
    "Orc": "Durotar",
    "Troll": "Durotar",
    "Tauren": "Mulgore",
    "Undead": "Tirisfal Glades",
    "Skyborne (Windshaper)": "Zephras Isle",
}

RACE_CLASSES = {
    "Orc": ("Hunter", "Mage", "Rogue", "Shaman", "Warlock", "Warrior"),
    "Troll": ("Hunter", "Mage", "Priest", "Rogue", "Shaman", "Warlock", "Warrior"),
    "Tauren": ("Druid", "Hunter", "Shaman", "Warrior"),
    "Undead": ("Mage", "Paladin", "Priest", "Rogue", "Warlock", "Warrior"),
    "Skyborne (Windshaper)": ("Druid", "Hunter", "Rogue", "Shaman", "Warrior"),
}
