"""Original, Warcraft-inspired Ogre voice library for Hyped Ogre.

Style inspiration: Warcraft II ogre/ogre-mage and Warcraft III neutral ogre.
These are new lines, not copies of in-game dialogue. Category-specific keys
allow safe expansion without rewriting command logic.
"""
import hashlib
import random

PHRASES = {
    "daily_prompt": (
        "Think you got high roll?",
        "Coin waiting. Who brave?",
        "One roll, one chance. Go on.",
        "Big number today? Ogre watching.",
        "Dice ready. You ready?",
        "Me got good feeling. Maybe.",
        "Someone get coin. Could be you.",
        "Roll first, brag later.",
        "Ogre keeping eye on dice.",
        "Lucky gamer, show yourself.",
        "Try luck. No promises.",
        "Good roll make Ogre smile.",
        "Coin shiny. Roll higher.",
        "You roll. Ogre count.",
        "Who taking coin tonight?",
        "Me think big roll coming.",
        "No roll, no coin. Simple.",
        "Ogre say give dice a go.",
        "Biggest number wins. Easy.",
        "You feeling lucky, eh?",
        "One little roll. Big shiny coin.",
        "Ogre got dice. You got courage?",
        "Me smell lucky numbers.",
        "Who got mighty rolling hand?",
        "Roll now. Counting later."
    ),
    "coin_winner": (
        "{name} highest roll for **{day}** with **{roll}**. He keep coin {coin}.",
        "{name} rolled biggest on **{day}**: **{roll}**. Coin goes to him {coin}.",
        "{name} won **{day}** with **{roll}**. Another shiny coin {coin}.",
        "Best roll on **{day}** was {name} with **{roll}**. Coin is his {coin}.",
        "{name} got **{roll}** on **{day}**. No one beat it. He take coin {coin}.",
        "{name} rolled **{roll}** on **{day}**. Ogre checked twice. Coin is his {coin}.",
        "{name} top of **{day}** with **{roll}**. Shiny coin earned {coin}.",
        "**{day}** belonged to {name}. **{roll}** wins coin {coin}.",
        "Ogre counted **{day}** rolls. {name} highest with **{roll}**. Coin his {coin}.",
        "{name} had big **{roll}** on **{day}**. Big enough for coin {coin}.",
        "**{roll}** wins **{day}** for {name}. He keep coin {coin}.",
        "{name} outrolled everyone on **{day}**: **{roll}**. Coin secured {coin}.",
        "{name} held top roll on **{day}** with **{roll}**. Ogre hand over coin {coin}.",
        "On **{day}**, {name} rolled **{roll}**. Highest wins. Coin his {coin}.",
        "{name} got lucky on **{day}**. **{roll}** takes coin {coin}.",
        "Biggest **{day}** number: **{roll}**, rolled by {name}. Coin goes there {coin}.",
        "{name} kept lead on **{day}** with **{roll}**. Ogre give coin {coin}.",
        "**{day}** rolls done. {name} wins with **{roll}**. Take shiny coin {coin}.",
        "{name} got highest **{day}** roll, **{roll}**. Ogre approve {coin}.",
        "Ogre checked scores. {name} top on **{day}** with **{roll}**. Coin earned {coin}."
    ),
    "coin_winner_ogre": (
        "Ogre rolled highest on **{day}**: **{roll}**. Me keep own coin {coin}.",
        "**{day}** belong to Ogre! **{roll}** wins. Coin stay with me {coin}.",
        "Ogre got **{roll}** on **{day}**. No one beat Ogre. Coin mine {coin}.",
        "Best **{day}** roll was me: **{roll}**. Me put coin in pocket {coin}.",
        "Ogre win **{day}** with **{roll}**. What? Ogre can win too {coin}.",
        "Me rolled **{roll}** on **{day}**. Ogre keep shiny coin {coin}.",
        "**{day}** finished. Ogre highest with **{roll}**. Me happy {coin}.",
        "Ogre top roll on **{day}**, **{roll}**. Coin stays home {coin}.",
        "Me count rolls. Me win **{day}** with **{roll}**. Fair is fair {coin}.",
        "Ogre rolled **{roll}** on **{day}**. Coin mine today {coin}."
    ),
    "roll_high": (
        "Ooh. Big roll.",
        "That one got muscle.",
        "Ogre like that number.",
        "Dice did good.",
        "Strong throw. Very strong.",
        "Me impressed. Little bit.",
        "Someone aiming for coin.",
        "That roll got teeth.",
        "Big number. Me see it.",
        "Ogre nods approvingly.",
        "Now that serious roll.",
        "You make other dice nervous.",
        "Good throw, gamer.",
        "Ogre count. Yes, big.",
        "Keep that luck safe."
    ),
    "roll_low": (
        "Oof. Dice sleepy.",
        "That number fell over.",
        "Ogre seen bigger pebbles.",
        "Dice not feeling brave.",
        "Me not say anything. Much.",
        "Tiny number, big courage.",
        "Better luck next sunset.",
        "Ogre count again. Still small.",
        "At least dice survived.",
        "That roll need breakfast.",
        "Hmm. Dice tired today.",
        "Ogre shrug. Tomorrow again.",
        "Numbers go up to hundred, you know.",
        "Bad luck. Still one roll.",
        "Me give sympathetic grunt."
    ),
    "roll_100": (
        "Hundred! Ogre eyes go wide.",
        "Perfect roll. Me counted twice.",
        "Biggest number! No arguing.",
        "Dice hit ceiling. **100**.",
        "Ogre impressed. Truly.",
        "That roll made noise.",
        "One hundred. Nice and shiny.",
        "Me cannot count higher than that.",
        "Hundred! Even other head agrees.",
        "Me put this on big-roll shelf."
    ),
    "roll_duplicate": (
        "You rolled already today.",
        "One roll per day. Ogre remember.",
        "Nice try. Dice finished.",
        "Come back after midnight.",
        "Ogre count one. You done.",
        "Another roll? Tomorrow.",
        "No second dice. Rules are rules.",
        "Me not forget first roll.",
        "One turn each, gamer.",
        "Dice needs sleep now."
    ),
    "leaderboard": (
        "Ogre counted every coin.",
        "Shiny coins. Me like.",
        "Big stack of lucky gamers.",
        "Numbers checked. Probably twice.",
        "Who got most shiny things?",
        "Ogre keep score.",
        "Coin pile getting taller.",
        "Some gamers roll very lucky.",
        "This where shiny coins live.",
        "Me guard this list."
    ),
    "stats": (
        "Ogre fetched your numbers.",
        "Here your roll story.",
        "Me counted all these.",
        "Numbers say plenty.",
        "Your dice leave tracks.",
        "Ogre got records.",
        "Your rolling history, right here.",
        "Me keep score carefully.",
        "Look at all them numbers.",
        "Stats ready. Ogre done maths."
    ),
    "empty": (
        "Nothing here yet.",
        "No numbers for Ogre to count.",
        "Empty. For now.",
        "Me check twice. Still nothing.",
        "No coin pile yet.",
        "Nobody earned one. Yet.",
        "Ogre waiting for first winner.",
        "This list need lucky gamer.",
        "Me got empty hands.",
        "Come back after some rolls."
    ),
    "reset": (
        "New day. Fresh dice.",
        "Midnight. Ogre reset board.",
        "Old rolls finished. New ones begin.",
        "New chance at shiny coin.",
        "Ogre wiped slate clean.",
        "Yesterday done. Roll again.",
        "Fresh day. Big numbers waiting.",
        "Another day, another coin.",
        "Dice wake up at midnight.",
        "Me open new roll day."
    ),
    "error": (
        "Something broke. Ogre looking.",
        "Ogre tried. Not working.",
        "Hmm. That not right.",
        "Me need another try.",
        "Dice machine grumpy.",
        "Ogre hit problem.",
        "Little trouble. Try again.",
        "Me checking what went wrong.",
        "Something stuck. Not the dice.",
        "Ogre fix soon. Maybe."
    ),
    "success": (
        "Done. Ogre approve.",
        "All sorted.",
        "Me got it.",
        "Good. That finished.",
        "Ogre mark it done.",
        "Right. Done now.",
        "Done and counted.",
        "All good, gamer.",
        "Ogre nods.",
        "That taken care of."
    ),
    "two_heads": (
        "One head says roll. Other says roll bigger.",
        "Me count. Other head checks.",
        "Left head likes coin. Right head also.",
        "Two heads. One leaderboard.",
        "Me say lucky. Other says maybe.",
        "One head watches dice. Other watches snacks.",
        "Me think big number. Other head doubts.",
        "Both heads agree. That rare.",
        "Other head says keep counting.",
        "One head remembers. Other head forgot."
    )
}
def pick(category, *, seed=None, **values):
    """Pick a phrase; deterministic for a supplied day/event seed.

    A deterministic day seed keeps public daily panels stable across restarts.
    Without a seed, picks independently for events that want variety.
    """
    entries = PHRASES[category]
    if seed is None:
        line = random.choice(entries)
    else:
        digest = hashlib.sha256(f"{category}:{seed}".encode("utf-8")).digest()
        line = entries[int.from_bytes(digest[:8], "big") % len(entries)]
    return line.format(**values)


def count_label(n, noun="coin"):
    return f"{n} {noun if n == 1 else noun + 's'}"
