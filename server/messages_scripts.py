"""AppleScript for the Messages helpers (System Events / Accessibility).

Every script is passed to `osascript` one line per `-e`, and everything that
comes from the user (names, message text, the reply) arrives as *argv*, never
in the source. argv[1] names the action ("mark-read", "tapback", "reply", …)
so osascript never mistakes a value for an option; the rest are tagged values:

    L:<name>   a conversation name that may match the start of a sidebar label
    N:<name>   a name that has to match a whole label line (or "Name, …")
    T:<text>   the message bubble's label contains this
    W:<word>   a bubble label line equals this (attachments: "Photo", …)
    S:<name>   the bubble's sender (preferred when labels mention it)
    K:<n>      skip this many newer sender+text matches
    J:<n>      skip this many newer text-only matches
    R:<word>   the tapback, by its button/menu description ("Heart", "Love"…)
    M:<text>   the reply to put in Messages' reply field

Every script returns one short code (see messages_ui.interpret_*). Nothing here
ever uses `keystroke`: the reply text goes in through the field's AXValue or,
failing that, the clipboard (saved and restored), and the only keys pressed are
Escape, Return and ⌘V.

House rules (Harry's Mac, macOS 26: AppleScript identifiers are case-insensitive
and System Events terms win inside a tell, so a local called `outLines` *was*
System Events' `outlines` class and every write to it failed with -10006):

- every multi-letter script-local variable and handler parameter starts with
  `bc` (argv is the only exception); handlers are always called with `my`;
- every `tell application "System Events"` is one line that only gets a
  value into a `bc` local or performs one action; strings, lists and
  coercions are built outside;
- `on error` branches only record: return a tagged string, set a flag or the
  error number, or leave the loop.

tests/test_messages_ui.py checks all three.
"""

from __future__ import annotations


def lines(source: str) -> tuple[str, ...]:
    """One -e argument per line; indentation and full-line `--` comments dropped."""
    out = []
    for line in source.splitlines():
        text = line.strip()
        if text and not text.startswith("--"):
            out.append(text)
    return tuple(out)


# --------------------------------------------------------------------------- shared handlers

RUN = """
on run argv
    set bcPrevApp to my frontBundle()
    set bcAnswer to "error-late:unknown"
    try
        set bcAnswer to my act(argv)
    on error bcErrMsg number bcErrNum
        set bcAnswer to "error-late:" & (bcErrNum as text)
    end try
    my restoreFront(bcPrevApp)
    return bcAnswer
end run
"""

ARGS = """
on tagged(argv, bcTag)
    set bcOut to {}
    set bcPrefix to bcTag & ":"
    set bcLen to length of bcPrefix
    repeat with bcArg in argv
        set bcS to bcArg as text
        if (length of bcS) > bcLen and bcS starts with bcPrefix then
            set bcOut to bcOut & {text (bcLen + 1) thru -1 of bcS}
        end if
    end repeat
    return bcOut
end tagged

on taggedNumber(argv, bcTag)
    set bcVals to my tagged(argv, bcTag)
    if bcVals is {} then return 0
    try
        return (item 1 of bcVals) as integer
    on error
        return 0
    end try
end taggedNumber
"""

ELEMENTS = """
on asText(bcValue)
    if bcValue is missing value then return ""
    try
        return bcValue as text
    on error
        return ""
    end try
end asText

on roleOf(bcEl)
    set bcRole to missing value
    try
        tell application "System Events" to set bcRole to role of bcEl
    end try
    return my asText(bcRole)
end roleOf

on labelOf(bcEl)
    set bcDesc to missing value
    set bcTitle to missing value
    set bcValue to missing value
    try
        tell application "System Events" to set bcDesc to description of bcEl
    end try
    try
        tell application "System Events" to set bcTitle to name of bcEl
    end try
    try
        tell application "System Events" to set bcValue to value of bcEl
    end try
    return my asText(bcDesc) & linefeed & my asText(bcTitle) & linefeed & my asText(bcValue) & linefeed
end labelOf

on frameOf(bcEl)
    set bcPos to missing value
    set bcSize to missing value
    try
        tell application "System Events" to set bcPos to position of bcEl
        tell application "System Events" to set bcSize to size of bcEl
    end try
    if bcPos is missing value or bcSize is missing value then return missing value
    try
        return {(item 1 of bcPos) as number, (item 2 of bcPos) as number, (item 1 of bcSize) as number, (item 2 of bcSize) as number}
    on error
        return missing value
    end try
end frameOf

on rightEdge(bcEl)
    set bcFrame to my frameOf(bcEl)
    if bcFrame is missing value then return 0
    return (item 1 of bcFrame) + (item 3 of bcFrame)
end rightEdge

on frameInside(bcInner, bcOuter)
    set x to item 1 of bcInner
    set y to item 2 of bcInner
    set u to item 1 of bcOuter
    set v to item 2 of bcOuter
    return x >= u - 2 and y >= v - 2 and x + (item 3 of bcInner) <= u + (item 3 of bcOuter) + 2 and y + (item 4 of bcInner) <= v + (item 4 of bcOuter) + 2
end frameInside

on sameBubble(bcFrameA, bcFrameB)
    if bcFrameA is missing value or bcFrameB is missing value then return false
    return my frameInside(bcFrameA, bcFrameB) or my frameInside(bcFrameB, bcFrameA)
end sameBubble

on lineMatches(bcLabel, bcWord)
    repeat with bcLine in paragraphs of bcLabel
        set bcT to bcLine as text
        if bcT is bcWord or bcT starts with (bcWord & ",") or bcT starts with (bcWord & " ") then return true
    end repeat
    return false
end lineMatches

on isSelected(bcEl)
    set bcSel to false
    try
        tell application "System Events" to set bcSel to value of attribute "AXSelected" of bcEl
    end try
    return bcSel is true
end isSelected

on cleanNames(bcList)
    set bcOut to {}
    repeat with bcX in bcList
        set bcV to contents of bcX
        if bcV is missing value then
            set bcOut to bcOut & {"-"}
        else
            set bcOut to bcOut & {my asText(bcV)}
        end if
    end repeat
    return bcOut
end cleanNames

on joinWith(bcList, bcSep)
    set bcSaved to AppleScript's text item delimiters
    set AppleScript's text item delimiters to bcSep
    set bcJoined to bcList as text
    set AppleScript's text item delimiters to bcSaved
    return bcJoined
end joinWith

on frontBundle()
    set bcBundle to missing value
    try
        tell application "System Events" to set bcBundle to bundle identifier of (first application process whose frontmost is true)
    end try
    return my asText(bcBundle)
end frontBundle

on restoreFront(bcPrevApp)
    if bcPrevApp is "" or bcPrevApp is "com.apple.MobileSMS" then return
    set bcMessagesFront to false
    try
        tell application "System Events" to set bcMessagesFront to frontmost of process "Messages"
    end try
    if bcMessagesFront is not true then return
    try
        tell application "System Events" to set frontmost of (first application process whose bundle identifier is bcPrevApp) to true
    end try
end restoreFront

on bringForward()
    try
        tell application "System Events" to set frontmost of process "Messages" to true
    end try
    delay 0.3
end bringForward

on pressEscape()
    my bringForward()
    try
        tell application "System Events" to key code 53
    end try
    delay 0.2
end pressEscape

on messagesState()
    set bcRunning to false
    tell application "System Events" to set bcRunning to (exists process "Messages")
    if bcRunning is not true then return "not-running"
    set bcCount to 0
    tell application "System Events" to set bcCount to count of windows of process "Messages"
    if bcCount is 0 then return "no-window"
    return "ok"
end messagesState
"""

# Find the conversation's row in the sidebar, in one pass over the window: a label line that is
# exactly one of the names (or "Name, …") wins, the earliest name first; the L: names may also
# match a label's beginning. Matches are climbed to their row; rows win over other elements.
SIDEBAR = """
on bestName(bcLabel, bcNames, bcStrict)
    repeat with i from 1 to (count of bcNames)
        set bcNeedle to (item i of bcNames) as text
        if (length of bcNeedle) > 0 then
            repeat with bcLine in paragraphs of bcLabel
                set bcT to bcLine as text
                if bcStrict then
                    if bcT is bcNeedle or bcT starts with (bcNeedle & ",") then return i
                else
                    if bcT starts with bcNeedle then return i
                end if
            end repeat
        end if
    end repeat
    return 0
end bestName

on findConversationRow(bcWin, bcStrictNames, bcLooseNames)
    set bcAllNames to bcLooseNames & bcStrictNames
    set bcLooseCount to count of bcLooseNames
    set bcBestStrict to 0
    set bcStrictRow to missing value
    set bcBestLoose to 0
    set bcLooseRow to missing value
    set bcFallback to missing value
    set bcSeen to 0
    set bcAll to {}
    tell application "System Events" to set bcAll to entire contents of bcWin
    repeat with bcItem in bcAll
        set bcEl to contents of bcItem
        set bcSeen to bcSeen + 1
        if bcSeen > 2500 then exit repeat
        if my roleOf(bcEl) is in {"AXRow", "AXCell", "AXButton", "AXGroup", "AXStaticText"} then
            set bcLabel to my labelOf(bcEl)
            set k to my bestName(bcLabel, bcAllNames, true)
            if k > 0 and (bcBestStrict = 0 or k < bcBestStrict) then
                set bcRowEl to my climbToRow(bcEl)
                if bcRowEl is not missing value then
                    set bcBestStrict to k
                    set bcStrictRow to bcRowEl
                    if k = 1 then exit repeat
                else if bcFallback is missing value then
                    set bcFallback to bcEl
                end if
            else if k = 0 and bcLooseCount > 0 then
                set j to my bestName(bcLabel, items 1 thru bcLooseCount of bcAllNames, false)
                if j > 0 and (bcBestLoose = 0 or j < bcBestLoose) then
                    set bcRowEl to my climbToRow(bcEl)
                    if bcRowEl is not missing value then
                        set bcBestLoose to j
                        set bcLooseRow to bcRowEl
                    end if
                end if
            end if
        end if
    end repeat
    if bcStrictRow is not missing value then return bcStrictRow
    if bcLooseRow is not missing value then return bcLooseRow
    return bcFallback
end findConversationRow

on climbToRow(bcEl)
    if my roleOf(bcEl) is in {"AXRow", "AXCell"} then return bcEl
    set bcUp to bcEl
    repeat 6 times
        set bcParent to missing value
        try
            tell application "System Events" to set bcParent to value of attribute "AXParent" of bcUp
        end try
        if bcParent is missing value then exit repeat
        set bcUp to bcParent
        if my roleOf(bcUp) is in {"AXRow", "AXCell"} then return bcUp
    end repeat
    return missing value
end climbToRow

on selectRow(bcRowEl)
    if my roleOf(bcRowEl) is not in {"AXRow", "AXCell"} then return false
    try
        tell application "System Events" to set value of attribute "AXSelected" of bcRowEl to true
    end try
    delay 0.3
    if my isSelected(bcRowEl) then return true
    try
        tell application "System Events" to perform action "AXPress" of bcRowEl
    end try
    delay 0.3
    return my isSelected(bcRowEl)
end selectRow
"""

# Find a message bubble in the transcript (right of the sidebar), newest first: walk the window
# backwards; nested elements of one bubble (same or enclosing frame) count once.
BUBBLES = """
on matchesBubble(bcLabel, bcNeedles, bcExacts)
    repeat with bcN in bcNeedles
        if bcLabel contains (bcN as text) then return true
    end repeat
    if (count of bcExacts) > 0 then
        repeat with bcLine in paragraphs of bcLabel
            set bcS to bcLine as text
            repeat with bcX in bcExacts
                set bcW to bcX as text
                if bcS is bcW or bcS starts with (bcW & ",") or bcS starts with (bcW & " ") or bcS contains (", " & bcW & ",") then return true
            end repeat
        end repeat
    end if
    return false
end matchesBubble

on mentionsAny(bcLabel, bcSenders)
    repeat with bcN in bcSenders
        if bcLabel contains (bcN as text) then return true
    end repeat
    return false
end mentionsAny

on findBubble(bcWin, bcMinX, bcNeedles, bcExacts, bcSenders, bcSkipSender, bcSkipAny)
    set bcAll to {}
    tell application "System Events" to set bcAll to entire contents of bcWin
    set bcSenderHits to 0
    set bcAnyHits to 0
    set bcAnyPick to missing value
    set bcPickedAt to 0
    set bcLastFrame to missing value
    set bcLastSenderFrame to missing value
    set bcExamined to 0
    repeat with i from (count of bcAll) to 1 by -1
        set bcExamined to bcExamined + 1
        if bcExamined > 3000 then exit repeat
        if bcAnyPick is not missing value and bcExamined - bcPickedAt > 400 then exit repeat
        set bcEl to item i of bcAll
        if my roleOf(bcEl) is in {"AXGroup", "AXStaticText", "AXTextArea", "AXTextField", "AXCell", "AXImage", "AXButton"} then
            set bcLabel to my labelOf(bcEl)
            if my matchesBubble(bcLabel, bcNeedles, bcExacts) then
                set bcFrame to my frameOf(bcEl)
                if bcFrame is not missing value then
                    if (item 1 of bcFrame) >= bcMinX then
                        if not my sameBubble(bcFrame, bcLastFrame) then
                            set bcLastFrame to bcFrame
                            set bcAnyHits to bcAnyHits + 1
                            if bcAnyHits = bcSkipAny + 1 then
                                set bcAnyPick to bcEl
                                set bcPickedAt to bcExamined
                                if (count of bcSenders) = 0 then return bcEl
                            end if
                        end if
                        if (count of bcSenders) > 0 then
                            if my mentionsAny(bcLabel, bcSenders) then
                                if not my sameBubble(bcFrame, bcLastSenderFrame) then
                                    set bcLastSenderFrame to bcFrame
                                    set bcSenderHits to bcSenderHits + 1
                                    if bcSenderHits = bcSkipSender + 1 then return bcEl
                                end if
                            end if
                        end if
                    end if
                end if
            end if
        end if
    end repeat
    return bcAnyPick
end findBubble
"""

MENUS = """
on openMenu(bcEl, bcWin)
    set bcHost to bcEl
    set bcShown to false
    repeat 4 times
        try
            tell application "System Events" to perform action "AXShowMenu" of bcHost
            set bcShown to true
        end try
        if bcShown then exit repeat
        set bcParent to missing value
        try
            tell application "System Events" to set bcParent to value of attribute "AXParent" of bcHost
        end try
        if bcParent is missing value then exit repeat
        set bcHost to bcParent
    end repeat
    if not bcShown then return missing value
    delay 0.4
    set bcMenu to missing value
    try
        tell application "System Events" to set bcMenu to menu 1 of bcHost
    end try
    if bcMenu is missing value then
        try
            tell application "System Events" to set bcMenu to menu 1 of bcWin
        end try
    end if
    return bcMenu
end openMenu

on closeMenu(bcMenu)
    set bcCancelled to false
    try
        tell application "System Events" to perform action "AXCancel" of bcMenu
        set bcCancelled to true
    end try
    if not bcCancelled then my pressEscape()
end closeMenu

on menuItemStarting(bcMenu, bcPrefixes)
    set bcEntries to {}
    try
        tell application "System Events" to set bcEntries to menu items of bcMenu
    end try
    repeat with bcEntry in bcEntries
        set bcMi to contents of bcEntry
        set bcTitle to missing value
        try
            tell application "System Events" to set bcTitle to name of bcMi
        end try
        set bcT to my asText(bcTitle)
        repeat with bcP in bcPrefixes
            if bcT starts with (bcP as text) then return bcMi
        end repeat
    end repeat
    return missing value
end menuItemStarting

on menuItemMatching(bcMenu, bcSynonyms)
    set bcAll to {}
    try
        tell application "System Events" to set bcAll to entire contents of bcMenu
    end try
    repeat with bcItem in bcAll
        set bcEl to contents of bcItem
        if my roleOf(bcEl) is in {"AXMenuItem", "AXButton", "AXRadioButton", "AXCheckBox"} then
            set bcLabel to my labelOf(bcEl)
            repeat with bcS in bcSynonyms
                if my lineMatches(bcLabel, bcS as text) then return bcEl
            end repeat
        end if
    end repeat
    return missing value
end menuItemMatching
"""

TAPBACK_BUTTONS = """
on findReactionButton(bcSynonyms)
    set bcWins to {}
    try
        tell application "System Events" to set bcWins to windows of process "Messages"
    end try
    repeat with bcW in bcWins
        set bcWinRef to contents of bcW
        set bcAll to {}
        try
            tell application "System Events" to set bcAll to entire contents of bcWinRef
        end try
        set bcExamined to 0
        repeat with i from (count of bcAll) to 1 by -1
            set bcExamined to bcExamined + 1
            if bcExamined > 1500 then exit repeat
            set bcEl to item i of bcAll
            if my roleOf(bcEl) is in {"AXButton", "AXRadioButton", "AXCheckBox"} then
                set bcLabel to my labelOf(bcEl)
                repeat with bcS in bcSynonyms
                    if my lineMatches(bcLabel, bcS as text) then return bcEl
                end repeat
            end if
        end repeat
    end repeat
    return missing value
end findReactionButton
"""

TEXT_FIELDS = """
on focusedTextField()
    set bcField to missing value
    try
        tell application "System Events" to set bcField to value of attribute "AXFocusedUIElement" of process "Messages"
    end try
    if bcField is missing value then return missing value
    if my roleOf(bcField) is in {"AXTextArea", "AXTextField"} then return bcField
    return missing value
end focusedTextField

on fieldText(bcField)
    set bcValue to missing value
    try
        tell application "System Events" to set bcValue to value of bcField
    on error
        return "?"
    end try
    return my asText(bcValue)
end fieldText
"""

CLIPBOARD = """
on clipboardIsText()
    set bcInfo to {}
    try
        set bcInfo to (clipboard info)
    on error
        return false
    end try
    repeat with bcPair in bcInfo
        set bcKind to item 1 of bcPair
        if bcKind is not in {string, Unicode text, text, «class utf8», «class ut16»} then return false
    end repeat
    return true
end clipboardIsText

on clipText()
    try
        return (the clipboard as text)
    on error
        return ""
    end try
end clipText

on setClip(bcText)
    try
        set the clipboard to bcText
    end try
end setClip
"""

# --------------------------------------------------------------------------- mark read

MARK_READ_ACT = """
on act(argv)
    set bcLooseNames to my tagged(argv, "L")
    set bcStrictNames to my tagged(argv, "N")
    set bcState to my messagesState()
    if bcState is not "ok" then return bcState
    tell application "System Events" to set bcWin to window 1 of process "Messages"
    set bcRowEl to my findConversationRow(bcWin, bcStrictNames, bcLooseNames)
    if bcRowEl is missing value then return "row-not-found"
    if my selectRow(bcRowEl) then
        set bcMark to missing value
        set bcEnabled to false
        try
            tell application "System Events" to set bcMark to menu item "Mark as Read" of menu 1 of menu bar item "Conversation" of menu bar 1 of process "Messages"
            tell application "System Events" to set bcEnabled to enabled of bcMark
        end try
        if bcEnabled is true then
            tell application "System Events" to click bcMark
            return "ok-menu"
        end if
    end if
    set bcMenu to my openMenu(bcRowEl, bcWin)
    if bcMenu is missing value then return "no-mark-item"
    set bcMark to my menuItemStarting(bcMenu, {"Mark as Read"})
    if bcMark is missing value then
        my closeMenu(bcMenu)
        return "no-mark-item"
    end if
    tell application "System Events" to click bcMark
    return "ok-context"
end act
"""

# --------------------------------------------------------------------------- tapback

TAPBACK_ACT = """
on act(argv)
    set bcLooseNames to my tagged(argv, "L")
    set bcStrictNames to my tagged(argv, "N")
    set bcNeedles to my tagged(argv, "T")
    set bcExacts to my tagged(argv, "W")
    set bcSenders to my tagged(argv, "S")
    set bcSkipSender to my taggedNumber(argv, "K")
    set bcSkipAny to my taggedNumber(argv, "J")
    set bcSynonyms to my tagged(argv, "R")
    if bcSynonyms is {} then return "no-kind"
    try
        set bcState to my messagesState()
        if bcState is not "ok" then return bcState
        tell application "System Events" to set bcWin to window 1 of process "Messages"
        set bcRowEl to my findConversationRow(bcWin, bcStrictNames, bcLooseNames)
        if bcRowEl is missing value then return "row-not-found"
        if not my selectRow(bcRowEl) then return "not-selected"
        delay 0.8
        set bcBubble to my findBubble(bcWin, my rightEdge(bcRowEl), bcNeedles, bcExacts, bcSenders, bcSkipSender, bcSkipAny)
        if bcBubble is missing value then return "bubble-not-found"
        set bcMenu to my openMenu(bcBubble, bcWin)
        if bcMenu is missing value then return "no-menu"
        set bcTapItem to my menuItemStarting(bcMenu, {"Tapback", "React"})
    on error bcErrMsg number bcErrNum
        return "error:" & (bcErrNum as text)
    end try
    if bcTapItem is missing value then
        set bcReact to my menuItemMatching(bcMenu, bcSynonyms)
        if bcReact is missing value then
            my closeMenu(bcMenu)
            return "no-tapback-item"
        end if
        tell application "System Events" to click bcReact
        return "chosen-menu"
    end if
    tell application "System Events" to click bcTapItem
    delay 0.6
    set bcButton to my findReactionButton(bcSynonyms)
    if bcButton is missing value then
        my pressEscape()
        return "no-tapback-button"
    end if
    tell application "System Events" to perform action "AXPress" of bcButton
    return "chosen-overlay"
end act
"""

# --------------------------------------------------------------------------- reply
# Stage 1 can't change anything in Messages (a failure there means "send it the normal way").
# From stage 2 on the text may be in Messages, so failures are reported, never retried. Return is
# only pressed when the focused field holds exactly the reply.

REPLY_ACT = """
on act(argv)
    set bcLooseNames to my tagged(argv, "L")
    set bcStrictNames to my tagged(argv, "N")
    set bcNeedles to my tagged(argv, "T")
    set bcExacts to my tagged(argv, "W")
    set bcSenders to my tagged(argv, "S")
    set bcSkipSender to my taggedNumber(argv, "K")
    set bcSkipAny to my taggedNumber(argv, "J")
    set bcTexts to my tagged(argv, "M")
    if bcTexts is {} then return "no-text"
    set bcReply to item 1 of bcTexts
    try
        set bcState to my messagesState()
        if bcState is not "ok" then return bcState
        tell application "System Events" to set bcWin to window 1 of process "Messages"
        set bcRowEl to my findConversationRow(bcWin, bcStrictNames, bcLooseNames)
        if bcRowEl is missing value then return "row-not-found"
        if not my selectRow(bcRowEl) then return "not-selected"
        delay 0.8
        set bcBubble to my findBubble(bcWin, my rightEdge(bcRowEl), bcNeedles, bcExacts, bcSenders, bcSkipSender, bcSkipAny)
        if bcBubble is missing value then return "bubble-not-found"
        set bcMenu to my openMenu(bcBubble, bcWin)
        if bcMenu is missing value then return "no-menu"
        set bcReplyItem to my menuItemStarting(bcMenu, {"Reply"})
        if bcReplyItem is missing value then
            my closeMenu(bcMenu)
            return "no-reply-item"
        end if
        tell application "System Events" to click bcReplyItem
        delay 0.7
        set bcBox to my focusedTextField()
        if bcBox is missing value then return "no-reply-field"
        if (my fieldText(bcBox)) is not "" then return "field-busy"
    on error bcErrMsg number bcErrNum
        return "error:" & (bcErrNum as text)
    end try
    set bcHow to ""
    try
        tell application "System Events" to set value of bcBox to bcReply
    end try
    delay 0.2
    considering case
        if (my fieldText(bcBox)) is bcReply then set bcHow to "axvalue"
    end considering
    if bcHow is "" then
        if (my fieldText(bcBox)) is not "" then return "typed-unknown"
        if not my clipboardIsText() then return "clipboard-busy"
        my bringForward()
        try
            tell application "System Events" to set focused of bcBox to true
        end try
        delay 0.15
        set bcFocused to my focusedTextField()
        if bcFocused is missing value then return "nothing-typed"
        if (my fieldText(bcFocused)) is not "" then return "nothing-typed"
        set bcSaved to my clipText()
        my setClip(bcReply)
        set bcPasted to true
        try
            tell application "System Events" to key code 9 using command down
        on error
            set bcPasted to false
        end try
        delay 0.4
        my setClip(bcSaved)
        if not bcPasted then return "typed-unknown"
        set bcFocused to my focusedTextField()
        if bcFocused is missing value then return "typed-unknown"
        considering case
            if (my fieldText(bcFocused)) is bcReply then set bcHow to "paste"
        end considering
        if bcHow is "" then return "typed-unknown"
    end if
    my bringForward()
    try
        tell application "System Events" to set focused of bcBox to true
    end try
    delay 0.1
    set bcFocused to my focusedTextField()
    if bcFocused is missing value then return "typed-not-sent"
    set bcReady to false
    considering case
        if (my fieldText(bcFocused)) is bcReply then set bcReady to true
    end considering
    if not bcReady then return "typed-not-sent"
    tell application "System Events" to key code 36
    delay 0.6
    if (my fieldText(bcFocused)) is "" then return "sent-" & bcHow
    return "returned-unconfirmed"
end act
"""

# --------------------------------------------------------------------------- permission probe
# Asks for "control System Events" (Automation) only; never touches Messages.

PROBE_SCRIPT = lines("""
on run argv
    set bcFront to missing value
    tell application "System Events" to set bcFront to name of first application process whose frontmost is true
    return "ready"
end run
""")

# --------------------------------------------------------------------------- doctor --messages-ui
# Prints roles, counts, booleans/lengths and menu/button titles only: never names, descriptions or
# values of conversations or messages. The bubble part opens the newest bubble's context menu,
# the Tapback picker (without choosing anything) and the Reply field (without typing), each
# closed again with Escape.

INSPECT_RUN = """
property bcBudget : 400

on run argv
    set bcAccessErr to 0
    try
        tell application "System Events" to set bcProbe to count of UI elements of process "Finder"
    on error bcErrMsg number bcErrNum
        set bcAccessErr to bcErrNum
    end try
    if bcAccessErr is not 0 then return "accessibility" & tab & "denied" & tab & (bcAccessErr as text)
    set bcReport to {"accessibility" & tab & "allowed"}
    set bcRunning to false
    tell application "System Events" to set bcRunning to (exists process "Messages")
    if bcRunning is not true then return my joinWith(bcReport & {"messages_running" & tab & "no"}, linefeed)
    set bcReport to bcReport & {"messages_running" & tab & "yes"}
    set bcWinCount to 0
    tell application "System Events" to set bcWinCount to count of windows of process "Messages"
    set bcReport to bcReport & {"windows" & tab & (bcWinCount as text)}
    set bcBarNames to {}
    try
        tell application "System Events" to set bcBarNames to name of every menu bar item of menu bar 1 of process "Messages"
    end try
    set bcReport to bcReport & {"menu_bar" & tab & my joinWith(my cleanNames(bcBarNames), " | ")}
    set bcConvNames to missing value
    try
        tell application "System Events" to set bcConvNames to name of every menu item of menu 1 of menu bar item "Conversation" of menu bar 1 of process "Messages"
    end try
    if bcConvNames is not missing value then set bcReport to bcReport & {"conversation_menu" & tab & my joinWith(my cleanNames(bcConvNames), " | ")}
    if bcWinCount > 0 then
        tell application "System Events" to set bcWin to window 1 of process "Messages"
        set bcReport to bcReport & {my walk(bcWin, 0, 8)}
        set bcReport to bcReport & {my rowStats(bcWin)}
        set bcReport to bcReport & {my inspectBubble(bcWin)}
    end if
    return my joinWith(bcReport, linefeed)
end run

on rowStats(bcWin)
    set bcRowCount to 0
    set bcWithDesc to 0
    set bcWithName to 0
    set bcWithValue to 0
    set bcMaxDesc to 0
    set bcMaxName to 0
    set bcMaxValue to 0
    set bcAll to {}
    tell application "System Events" to set bcAll to entire contents of bcWin
    repeat with bcItem in bcAll
        set bcEl to contents of bcItem
        if my roleOf(bcEl) is in {"AXRow", "AXCell"} then
            set bcRowCount to bcRowCount + 1
            set n to my textLength(bcEl, "description")
            if n > 0 then set bcWithDesc to bcWithDesc + 1
            if n > bcMaxDesc then set bcMaxDesc to n
            set n to my textLength(bcEl, "name")
            if n > 0 then set bcWithName to bcWithName + 1
            if n > bcMaxName then set bcMaxName to n
            set n to my textLength(bcEl, "value")
            if n > 0 then set bcWithValue to bcWithValue + 1
            if n > bcMaxValue then set bcMaxValue to n
        end if
    end repeat
    return "rows" & tab & bcRowCount & tab & bcWithDesc & tab & bcWithName & tab & bcWithValue & tab & bcMaxDesc & tab & bcMaxName & tab & bcMaxValue
end rowStats

on textLength(bcEl, bcWhich)
    set bcValue to missing value
    try
        if bcWhich is "description" then
            tell application "System Events" to set bcValue to description of bcEl
        else if bcWhich is "name" then
            tell application "System Events" to set bcValue to name of bcEl
        else
            tell application "System Events" to set bcValue to value of bcEl
        end if
    end try
    return length of (my asText(bcValue))
end textLength

on selectedRowEdge(bcWin)
    set bcAll to {}
    tell application "System Events" to set bcAll to entire contents of bcWin
    set bcSeen to 0
    repeat with bcItem in bcAll
        set bcEl to contents of bcItem
        set bcSeen to bcSeen + 1
        if bcSeen > 2500 then exit repeat
        if my roleOf(bcEl) is in {"AXRow", "AXCell"} then
            if my isSelected(bcEl) then return my rightEdge(bcEl)
        end if
    end repeat
    set bcFrame to my frameOf(bcWin)
    if bcFrame is missing value then return 0
    return (item 1 of bcFrame) + ((item 3 of bcFrame) * 0.3)
end selectedRowEdge

on actionsOf(bcEl)
    set bcActs to {}
    try
        tell application "System Events" to set bcActs to name of every action of bcEl
    end try
    return my cleanNames(bcActs)
end actionsOf

on newestBubble(bcWin, bcMinX)
    set bcAll to {}
    tell application "System Events" to set bcAll to entire contents of bcWin
    set bcExamined to 0
    repeat with i from (count of bcAll) to 1 by -1
        set bcExamined to bcExamined + 1
        if bcExamined > 1500 then exit repeat
        set bcEl to item i of bcAll
        if my roleOf(bcEl) is in {"AXGroup", "AXStaticText", "AXCell", "AXImage"} then
            set bcFrame to my frameOf(bcEl)
            if bcFrame is not missing value then
                if (item 1 of bcFrame) >= bcMinX then
                    if (my actionsOf(bcEl)) contains "AXShowMenu" then return bcEl
                end if
            end if
        end if
    end repeat
    return missing value
end newestBubble

on buttonLabels()
    set bcOut to {}
    set bcWins to {}
    try
        tell application "System Events" to set bcWins to windows of process "Messages"
    end try
    repeat with bcW in bcWins
        set bcWinRef to contents of bcW
        set bcAll to {}
        try
            tell application "System Events" to set bcAll to entire contents of bcWinRef
        end try
        set bcSeen to 0
        repeat with bcItem in bcAll
            set bcEl to contents of bcItem
            set bcSeen to bcSeen + 1
            if bcSeen > 3000 then exit repeat
            if my roleOf(bcEl) is in {"AXButton", "AXRadioButton", "AXCheckBox"} then
                set bcDesc to missing value
                try
                    tell application "System Events" to set bcDesc to description of bcEl
                end try
                set bcDescText to my asText(bcDesc)
                if bcDescText is not "" then set bcOut to bcOut & {bcDescText}
            end if
        end repeat
    end repeat
    return bcOut
end buttonLabels

on inspectBubble(bcWin)
    set bcPrevApp to my frontBundle()
    set bcBubble to my newestBubble(bcWin, my selectedRowEdge(bcWin))
    if bcBubble is missing value then return "bubble" & tab & "none"
    set bcReport to {"bubble" & tab & my roleOf(bcBubble) & tab & my joinWith(my actionsOf(bcBubble), ",")}
    set bcMenu to my openMenu(bcBubble, bcWin)
    if bcMenu is missing value then return my joinWith(bcReport & {"bubble_menu" & tab & "(none)"}, linefeed)
    set bcTitles to {}
    try
        tell application "System Events" to set bcTitles to name of every menu item of bcMenu
    end try
    set bcReport to bcReport & {"bubble_menu" & tab & my joinWith(my cleanNames(bcTitles), " | ")}
    set bcTapItem to my menuItemStarting(bcMenu, {"Tapback", "React"})
    if bcTapItem is missing value then
        set bcReport to bcReport & {"tapback_item" & tab & "no"}
        my closeMenu(bcMenu)
    else
        set bcReport to bcReport & {"tapback_item" & tab & "yes"}
        set bcBefore to my buttonLabels()
        tell application "System Events" to click bcTapItem
        delay 0.6
        set bcAfter to my buttonLabels()
        set bcFresh to {}
        repeat with bcX in bcAfter
            set bcDescText to contents of bcX
            if bcDescText is not in bcBefore then set bcFresh to bcFresh & {bcDescText}
        end repeat
        set bcReport to bcReport & {"tapback_buttons" & tab & my joinWith(bcFresh, " | ")}
        my pressEscape()
    end if
    set bcMenu to my openMenu(bcBubble, bcWin)
    if bcMenu is not missing value then
        set bcReplyItem to my menuItemStarting(bcMenu, {"Reply"})
        if bcReplyItem is missing value then
            set bcReport to bcReport & {"reply_item" & tab & "no"}
            my closeMenu(bcMenu)
        else
            set bcReport to bcReport & {"reply_item" & tab & "yes"}
            tell application "System Events" to click bcReplyItem
            delay 0.7
            set bcField to my focusedTextField()
            if bcField is missing value then
                set bcReport to bcReport & {"reply_field" & tab & "none"}
            else
                set bcSettable to false
                try
                    tell application "System Events" to set bcSettable to settable of attribute "AXValue" of bcField
                end try
                set bcReport to bcReport & {"reply_field" & tab & my roleOf(bcField) & tab & (bcSettable as text)}
            end if
            my pressEscape()
        end if
    end if
    my restoreFront(bcPrevApp)
    return my joinWith(bcReport, linefeed)
end inspectBubble

on walk(bcEl, bcDepth, bcMaxDepth)
    if bcBudget is less than or equal to 0 then return ""
    set bcBudget to bcBudget - 1
    set bcRole to my roleOf(bcEl)
    if bcRole is "" then set bcRole to "?"
    set bcKids to {}
    try
        tell application "System Events" to set bcKids to UI elements of bcEl
    end try
    set bcReport to {"tree" & tab & (bcDepth as text) & tab & bcRole & tab & ((count of bcKids) as text)}
    if bcDepth < bcMaxDepth then
        set i to 0
        repeat with bcKid in bcKids
            set i to i + 1
            if i > 12 or bcBudget is less than or equal to 0 then exit repeat
            set bcReport to bcReport & {my walk(contents of bcKid, bcDepth + 1, bcMaxDepth)}
        end repeat
    end if
    return my joinWith(bcReport, linefeed)
end walk
"""

MARK_READ_SCRIPT = lines(RUN + ARGS + ELEMENTS + SIDEBAR + MENUS + MARK_READ_ACT)
TAPBACK_SCRIPT = lines(RUN + ARGS + ELEMENTS + SIDEBAR + BUBBLES + MENUS + TAPBACK_BUTTONS + TAPBACK_ACT)
REPLY_SCRIPT = lines(RUN + ARGS + ELEMENTS + SIDEBAR + BUBBLES + MENUS + TEXT_FIELDS + CLIPBOARD + REPLY_ACT)
INSPECT_SCRIPT = lines(INSPECT_RUN + ELEMENTS + MENUS + TEXT_FIELDS)
