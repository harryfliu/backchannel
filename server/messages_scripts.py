"""AppleScript for the Messages helpers (System Events / Accessibility).

Every script is passed to `osascript` one line per `-e`, and everything that
comes from the user (names, message text, the reply) arrives as *argv*, never
in the source. argv[1] names the action ("mark-read", "mark-unread", "tapback", "reply", …)
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


# Where things sit in Messages' window on macOS 26.3: child indexes from the window, outermost first
# (mapped with `doctor --messages-tree`). Each becomes one nested reference, so System Events reaches
# the spot in a single request instead of one request per level; scripts still check the spot's id.
SPOTS = {
    "list": (1, 1, 1, 1, 1, 2, 2, 1, 1, 2, 1, 1, 1),
    "transcript": (1, 1, 1, 1, 1, 2, 1, 1, 1, 1, 1, 2),
    "entry": (1, 1, 1, 1, 1, 2, 1, 1, 1, 1, 1, 1, 1),
    "title": (1, 1, 1, 1, 1, 1, 3, 1, 2),
}


def spot_ref(path: tuple[int, ...], base: str = "bcWin") -> str:
    return " of ".join(f"UI element {n}" for n in reversed(path)) + f" of {base}"


# --------------------------------------------------------------------------- shared handlers

RUN = """
property bcTrail : ""
property bcUp : 0
property bcMembers : {}
property bcExtra : ""
property bcKeepFront : false
property bcRestoreTo : ""
property bcFrontWas : ""

on run argv
    set bcMembers to my tagged(argv, "G")
    set bcPrevApp to my frontBundle()
    set bcFrontWas to bcPrevApp
    set bcAnswer to "error-late:unknown"
    try
        set bcAnswer to my act(argv)
    on error bcErrMsg number bcErrNum
        set bcAnswer to "error-late:" & (bcErrNum as text)
    end try
    if bcRestoreTo is not "" then set bcPrevApp to bcRestoreTo
    if not bcKeepFront then my restoreFront(bcPrevApp)
    set bcLine to bcAnswer
    if bcTrail is not "" then set bcLine to bcAnswer & "|" & bcTrail
    if bcExtra is not "" then set bcLine to bcLine & linefeed & bcExtra
    return bcLine
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

on splitOn(bcText, bcSep)
    set bcSaved to AppleScript's text item delimiters
    set AppleScript's text item delimiters to bcSep
    set bcItems to text items of bcText
    set AppleScript's text item delimiters to bcSaved
    return bcItems
end splitOn

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
    if bcCount is 0 then
        try
            tell application "Messages" to reopen
        end try
        delay 1.2
        tell application "System Events" to set bcCount to count of windows of process "Messages"
    end if
    if bcCount is 0 then return "no-window"
    return "ok"
end messagesState
"""

# Find the conversation's row in the sidebar, in one pass over the window: a label line that is
# exactly one of the names (or "Name, …") wins, the earliest name first; the L: names may also
# match a label's beginning. Matches are climbed to their row; rows win over other elements.
SIDEBAR = """
on attrText(bcEl, bcName)
    set bcValue to missing value
    try
        tell application "System Events" to set bcValue to value of attribute bcName of bcEl
    end try
    return my asText(bcValue)
end attrText

on atPath(bcRoot, bcPath)
    if bcRoot is missing value then return missing value
    set bcEl to bcRoot
    repeat with bcStep in bcPath
        set bcNum to contents of bcStep
        set bcNext to missing value
        try
            tell application "System Events" to set bcNext to UI element bcNum of bcEl
        end try
        if bcNext is missing value then return missing value
        set bcEl to bcNext
    end repeat
    return bcEl
end atPath

-- Breadth-first by accessibility id, one request per element for its children's ids, with a cap.
on searchId(bcRoot, bcWanted)
    set bcLevel to {bcRoot}
    set bcSpent to 0
    repeat 18 times
        set bcNext to {}
        repeat with bcNode in bcLevel
            set bcSpent to bcSpent + 1
            if bcSpent > 90 then return missing value
            set bcHere to contents of bcNode
            set bcKids to {}
            set bcIds to {}
            try
                tell application "System Events" to set bcKids to UI elements of bcHere
            end try
            try
                tell application "System Events" to set bcIds to value of attribute "AXIdentifier" of every UI element of bcHere
            end try
            repeat with i from 1 to count of bcKids
                if i is less than or equal to (count of bcIds) then
                    if my asText(item i of bcIds) is bcWanted then return item i of bcKids
                end if
            end repeat
            set bcNext to bcNext & bcKids
        end repeat
        if bcNext is {} then return missing value
        set bcLevel to bcNext
    end repeat
    return missing value
end searchId

-- The conversation list and the open chat. On macOS 26.3 they sit at these child-index paths
-- (checked by id); with another layout (say, the details panel open) a capped search finds them.
on spotList(bcWin)
    set bcEl to missing value
    try
        tell application "System Events" to set bcEl to __SPOT_list__
    end try
    if my attrText(bcEl, "AXIdentifier") is "ConversationList" then return bcEl
    return my searchId(bcWin, "ConversationList")
end spotList

on spotTranscript(bcWin)
    set bcEl to missing value
    try
        tell application "System Events" to set bcEl to __SPOT_transcript__
    end try
    if my attrText(bcEl, "AXIdentifier") is "TranscriptCollectionView" then return bcEl
    return my searchId(bcWin, "TranscriptCollectionView")
end spotTranscript

on spotTitle(bcWin)
    set bcEl to missing value
    try
        tell application "System Events" to set bcEl to __SPOT_title__
    end try
    if my attrText(bcEl, "AXIdentifier") is "ConversationTitle" then return bcEl
    return my searchId(bcWin, "ConversationTitle")
end spotTitle

-- The conversation's item in the list, by label, read for every item in one request. Labels look
-- like "Name, [Unread, ]preview, time" (pinned: "Name, [Unread, preview, ]Pinned"). Any name may
-- match the whole label or its "Name, " start; L names may also match its beginning. The earliest
-- name wins.
on findRow(bcList, bcStrictNames, bcLooseNames)
    set bcAt to item 1 of (my findRowScrolling(bcList, bcStrictNames, bcLooseNames, 12))
    if bcAt is 0 then return missing value
    return my atPath(bcList, {bcAt})
end findRow

on findRowIndex(bcList, bcStrictNames, bcLooseNames)
    set bcDescs to {}
    set bcRoles to {}
    try
        tell application "System Events" to set bcDescs to description of every UI element of bcList
    end try
    try
        tell application "System Events" to set bcRoles to role of every UI element of bcList
    end try
    set bcAllNames to bcLooseNames & bcStrictNames
    set bcLooseCount to count of bcLooseNames
    set bcBest to 0
    set bcBestAt to 0
    set bcLooseBest to 0
    set bcLooseAt to 0
    repeat with i from 1 to count of bcDescs
        set bcRole to ""
        if i is less than or equal to (count of bcRoles) then set bcRole to my asText(item i of bcRoles)
        if bcRole is not "AXGroup" then
            set bcLabel to my asText(item i of bcDescs)
            repeat with k from 1 to count of bcAllNames
                set bcName to my asText(item k of bcAllNames)
                if bcName is not "" then
                    if bcLabel is bcName or bcLabel starts with (bcName & ", ") then
                        if bcBest is 0 or k < bcBest then
                            set bcBest to k
                            set bcBestAt to i
                        end if
                    else if k is less than or equal to bcLooseCount and bcLabel starts with bcName then
                        if bcLooseBest is 0 or k < bcLooseBest then
                            set bcLooseBest to k
                            set bcLooseAt to i
                        end if
                    end if
                end if
            end repeat
        end if
    end repeat
    if bcBestAt is 0 then set bcBestAt to bcLooseAt
    if bcBestAt is 0 and bcMembers is not {} then set bcBestAt to my membersIndex(bcDescs, bcRoles)
    return bcBestAt
end findRowIndex

-- An unnamed group's item: its label names every member (argv G:, each "nickname|first|full name"),
-- in any order, starting with one of them. The shortest such label wins.
on membersIndex(bcDescs, bcRoles)
    set bcPick to 0
    set bcPickLen to 0
    repeat with i from 1 to count of bcDescs
        set bcRole to ""
        if i is less than or equal to (count of bcRoles) then set bcRole to my asText(item i of bcRoles)
        if bcRole is not "AXGroup" then
            set bcLabel to my asText(item i of bcDescs)
            if my namesAll(bcLabel) then
                if bcPick is 0 or (length of bcLabel) < bcPickLen then
                    set bcPick to i
                    set bcPickLen to length of bcLabel
                end if
            end if
        end if
    end repeat
    return bcPick
end membersIndex

on namesAll(bcLabel)
    if bcMembers is {} or bcLabel is "" then return false
    set bcFirst to false
    repeat with bcMember in bcMembers
        set bcFound to false
        repeat with bcAlt in my splitOn(contents of bcMember, "|")
            set bcName to my asText(contents of bcAlt)
            if bcName is not "" and bcLabel contains bcName then
                set bcFound to true
                if bcLabel starts with bcName then set bcFirst to true
            end if
        end repeat
        if not bcFound then return false
    end repeat
    return bcFirst
end namesAll

-- ---- opening a conversation (tapbacks, replies)

on titleText(bcWin)
    return my attrText(my spotTitle(bcWin), "AXDescription")
end titleText

-- Is this conversation the one on screen? The title at the top of the chat is one of its names.
on titleMatches(bcWin, bcStrictNames, bcLooseNames)
    set bcTitle to my titleText(bcWin)
    if bcTitle is "" then return false
    repeat with bcName in (bcLooseNames & bcStrictNames)
        set bcWant to my asText(contents of bcName)
        if bcWant is not "" and bcTitle is bcWant then return true
    end repeat
    return my namesAll(bcTitle)
end titleMatches

-- The chat on screen is this conversation: its title says so, or its item is the only one selected.
on chatShown(bcWin, bcList, bcStrictNames, bcLooseNames)
    if my titleMatches(bcWin, bcStrictNames, bcLooseNames) then return true
    set bcAt to my findRowIndex(bcList, bcStrictNames, bcLooseNames)
    if bcAt is 0 then return false
    return my onlySelected(bcList, bcAt)
end chatShown

-- Positions of the selected items, like "3,14" ("-" for none), for the log.
on selectedList(bcList)
    set bcSels to my selectedFlags(bcList)
    set bcOut to {}
    repeat with i from 1 to count of bcSels
        if (item i of bcSels) is true then set bcOut to bcOut & {i as text}
    end repeat
    if bcOut is {} then return "-"
    return my joinWith(bcOut, ",")
end selectedList

-- Press a list item. Messages treats that like a Command-click: it toggles the item's selection.
on pressItem(bcList, bcAt)
    set bcItem to my atPath(bcList, {bcAt})
    if bcItem is missing value then return false
    set bcDone to false
    try
        tell application "System Events" to perform action "AXPress" of bcItem
        set bcDone to true
    end try
    return bcDone
end pressItem

-- Scroll a list (the sidebar or the open chat) a page: its items pass AXScrollUpByPage /
-- AXScrollDownByPage on to the scroll view. Not every item has them (the sidebar ends with section
-- groups), so the next few from that end are tried.
on pageList(bcList, bcAction)
    set bcCount to 0
    try
        tell application "System Events" to set bcCount to count of UI elements of bcList
    end try
    if bcCount is 0 then return false
    repeat with bcStep from 0 to 4
        set bcIndex to bcCount - bcStep
        if bcAction is "AXScrollUpByPage" then set bcIndex to 1 + bcStep
        if bcIndex < 1 or bcIndex > bcCount then exit repeat
        set bcItem to my atPath(bcList, {bcIndex})
        if bcItem is not missing value then
            try
                if bcAction is "AXScrollUpByPage" then
                    tell application "System Events" to perform action "AXScrollUpByPage" of bcItem
                else
                    tell application "System Events" to perform action "AXScrollDownByPage" of bcItem
                end if
                return true
            end try
        end if
    end repeat
    return false
end pageList

-- What the list shows, in short (item count, first and last labels), to tell when it stops moving.
on listKey(bcList)
    set bcDescs to {}
    try
        tell application "System Events" to set bcDescs to description of every UI element of bcList
    end try
    set bcCount to count of bcDescs
    if bcCount is 0 then return "0"
    return (bcCount as text) & ":" & my asText(item 1 of bcDescs) & ":" & my asText(item bcCount of bcDescs)
end listKey

-- Scroll the list back to the top (at most bcPages pages); how many pages it moved.
on listHome(bcList, bcPages)
    set bcSeen to my listKey(bcList)
    set bcMoved to 0
    repeat bcPages times
        if not my pageList(bcList, "AXScrollUpByPage") then exit repeat
        delay 0.3
        set bcNow to my listKey(bcList)
        if bcNow is bcSeen then exit repeat
        set bcSeen to bcNow
        set bcMoved to bcMoved + 1
    end repeat
    return bcMoved
end listHome

-- The conversation's item in the list. Messages only keeps the items in view (pinned chats and the
-- most recent ones), so if it isn't there: back to the top, then down a page at a time until it
-- shows up or the list ends. {position or 0, pages scrolled down}.
on findRowScrolling(bcList, bcStrictNames, bcLooseNames, bcPages)
    set bcAt to my findRowIndex(bcList, bcStrictNames, bcLooseNames)
    if bcAt > 0 then return {bcAt, 0}
    if (my listHome(bcList, bcPages)) > 0 then
        set bcAt to my findRowIndex(bcList, bcStrictNames, bcLooseNames)
        if bcAt > 0 then return {bcAt, 0}
    end if
    set bcSeen to my listKey(bcList)
    set bcPage to 0
    repeat bcPages times
        if not my pageList(bcList, "AXScrollDownByPage") then exit repeat
        set bcPage to bcPage + 1
        delay 0.35
        set bcAt to my findRowIndex(bcList, bcStrictNames, bcLooseNames)
        if bcAt > 0 then return {bcAt, bcPage}
        set bcNow to my listKey(bcList)
        if bcNow is bcSeen then exit repeat
        set bcSeen to bcNow
    end repeat
    return {0, bcPage}
end findRowScrolling

-- Bring the conversation up in Messages' main window. In order: it's already on screen; for a
-- one-to-one chat, Messages' own link (argv U:, imessage: or sms:); its item in the list (scrolled
-- to if needed) pressed, then any other selected item pressed off, then clicked with Messages in
-- front. Every step is checked with chatShown. Answers "ok", "row-not-found" or "not-selected";
-- bcTrail says how (step names and item positions only, for the log).
on openChat(bcWin, bcList, bcStrictNames, bcLooseNames, bcLinks)
    set bcTrace to {}
    if my chatShown(bcWin, bcList, bcStrictNames, bcLooseNames) then return my traced("ok", {"shown"})
    if bcLinks is not {} then
        set bcTitleBefore to my titleText(bcWin)
        set bcWinsBefore to my windowTotal()
        try
            open location (item 1 of bcLinks)
        end try
        set bcTrace to bcTrace & {"link"}
        repeat 14 times
            delay 0.5
            if my titleMatches(bcWin, bcStrictNames, bcLooseNames) then return my traced("ok", bcTrace & {"title"})
        end repeat
        set bcTitleAfter to my titleText(bcWin)
        set bcTrace to bcTrace & {"w" & (bcWinsBefore as text) & "/" & ((my windowTotal()) as text), "tl" & ((length of bcTitleBefore) as text) & "/" & ((length of bcTitleAfter) as text)}
        if bcTitleAfter is not "" and bcTitleAfter is not bcTitleBefore then return my traced("ok", bcTrace & {"changed"})
        set bcTrace to bcTrace & {"no", "tr" & my listKey(my spotTranscript(bcWin))}
        -- The window may have re-laid itself out: look the list up again.
        set bcList to my spotList(bcWin)
        if bcList is missing value then return my traced("not-selected", bcTrace & {"nolist"})
    end if
    set bcFound to my findRowScrolling(bcList, bcStrictNames, bcLooseNames, 12)
    set bcAt to item 1 of bcFound
    set bcTrace to bcTrace & {"row" & (bcAt as text) & "/" & ((item 2 of bcFound) as text)}
    if bcAt is 0 then return my traced("row-not-found", bcTrace)
    if my chatShown(bcWin, bcList, bcStrictNames, bcLooseNames) then return my traced("ok", bcTrace)
    if not my isSelected(my atPath(bcList, {bcAt})) then
        my pressItem(bcList, bcAt)
        delay 0.7
    end if
    set bcSelected to my selectedList(bcList)
    set bcTrace to bcTrace & {"p:" & bcSelected}
    if my chatShown(bcWin, bcList, bcStrictNames, bcLooseNames) then return my traced("ok", bcTrace)
    -- The item we found by name and pressed is the only one selected: its chat is on screen, even if
    -- the list moved or relabelled it so it isn't found by name again (the message search that
    -- follows still checks the chat's content).
    if bcSelected is (bcAt as text) then return my traced("ok", bcTrace & {"pressed"})
    set bcAt to my findRowIndex(bcList, bcStrictNames, bcLooseNames)
    if bcAt is 0 then return my traced("not-selected", bcTrace & {"lost"})
    set bcSels to my selectedFlags(bcList)
    repeat with i from 1 to count of bcSels
        if i is not bcAt and (item i of bcSels) is true then my pressItem(bcList, i)
    end repeat
    delay 0.7
    set bcTrace to bcTrace & {"o:" & my selectedList(bcList)}
    if my chatShown(bcWin, bcList, bcStrictNames, bcLooseNames) then return my traced("ok", bcTrace)
    my bringForward()
    set bcAt to my findRowIndex(bcList, bcStrictNames, bcLooseNames)
    set bcFrame to missing value
    if bcAt > 0 then set bcFrame to my frameOf(my atPath(bcList, {bcAt}))
    if bcFrame is not missing value then
        set bcPoint to {(item 1 of bcFrame) + ((item 3 of bcFrame) / 2), (item 2 of bcFrame) + ((item 4 of bcFrame) / 2)}
        try
            tell application "System Events" to click at bcPoint
        end try
        delay 0.7
    end if
    set bcTrace to bcTrace & {"c:" & my selectedList(bcList)}
    if my chatShown(bcWin, bcList, bcStrictNames, bcLooseNames) then return my traced("ok", bcTrace)
    return my traced("not-selected", bcTrace)
end openChat

on windowTotal()
    set bcCount to 0
    try
        tell application "System Events" to set bcCount to count of windows of process "Messages"
    end try
    return bcCount
end windowTotal

on traced(bcAnswer, bcTrace)
    set bcTrail to my joinWith(bcTrace, " ")
    return bcAnswer
end traced

on addTrail(bcText)
    if bcTrail is "" then
        set bcTrail to bcText
    else
        set bcTrail to bcTrail & " " & bcText
    end if
end addTrail

on selectedFlags(bcList)
    set bcSels to {}
    try
        tell application "System Events" to set bcSels to value of attribute "AXSelected" of every UI element of bcList
    end try
    return bcSels
end selectedFlags

on onlySelected(bcList, bcAt)
    set bcSels to my selectedFlags(bcList)
    if bcAt > (count of bcSels) then return false
    set bcCount to 0
    repeat with bcS in bcSels
        if (contents of bcS) is true then set bcCount to bcCount + 1
    end repeat
    return bcCount is 1 and (item bcAt of bcSels) is true
end onlySelected

-- The message entry area at the bottom of the chat. In reply mode Messages adds a preview of the
-- message being answered inside it, so its element count goes up.
on spotEntry(bcWin)
    set bcEl to missing value
    try
        tell application "System Events" to set bcEl to __SPOT_entry__
    end try
    if my attrText(bcEl, "AXIdentifier") is "MessageEntryView" then return bcEl
    return my searchId(bcWin, "MessageEntryView")
end spotEntry

-- In reply mode Messages adds a preview of the message being answered to the entry area; on macOS
-- 26.3 this group inside it goes from one child to two. One request.
on replyMarks(bcEntry)
    set bcN to 0
    try
        tell application "System Events" to set bcN to count of UI elements of UI element 1 of UI element 1 of UI element 2 of bcEntry
    end try
    return bcN
end replyMarks

-- An element's own accessibility shortcut by title ("Heart", "Reply", "Mark as Read"). Custom
-- actions describe themselves by title; their names read "Name:<title>" plus a target.
on customAction(bcEl, bcTitles)
    set bcAt to my actionIndex(bcEl, bcTitles)
    if bcAt is 0 then return false
    set bcDone to false
    try
        tell application "System Events" to perform action bcAt of bcEl
        set bcDone to true
    end try
    return bcDone
end customAction

-- The element's actions titled with an emoji (Messages offers its recent emoji as tapbacks that
-- way: "😂", "👀"…), space-separated. Titles of up to 8 characters, every one beyond Latin-1.
on emojiActions(bcEl)
    set bcDescs to {}
    try
        tell application "System Events" to set bcDescs to description of every action of bcEl
    end try
    set bcOut to {}
    repeat with bcD in bcDescs
        set bcText to my asText(contents of bcD)
        if bcText is not "" and (length of bcText) is less than or equal to 8 then
            set bcIds to {}
            try
                set bcIds to id of bcText
            end try
            if class of bcIds is not list then set bcIds to {bcIds}
            set bcWide to (count of bcIds) > 0
            repeat with bcId in bcIds
                if (contents of bcId) < 256 then set bcWide to false
            end repeat
            if bcWide then set bcOut to bcOut & {bcText}
        end if
    end repeat
    return my joinWith(bcOut, " ")
end emojiActions

on hasAction(bcEl, bcTitles)
    return (my actionIndex(bcEl, bcTitles)) > 0
end hasAction

-- Which of the element's actions has one of these titles: by description ("Heart", "Reply…"),
-- else by name ("Name:Heart" plus a target).
on actionIndex(bcEl, bcTitles)
    set bcDescs to {}
    try
        tell application "System Events" to set bcDescs to description of every action of bcEl
    end try
    set bcAt to my titleIndex(bcDescs, bcTitles, "")
    if bcAt > 0 then return bcAt
    set bcNames to {}
    try
        tell application "System Events" to set bcNames to name of every action of bcEl
    end try
    return my titleIndex(bcNames, bcTitles, "Name:")
end actionIndex

on titleIndex(bcList, bcTitles, bcPrefix)
    repeat with bcTitle in bcTitles
        set bcWant to bcPrefix & (contents of bcTitle)
        repeat with i from 1 to count of bcList
            if my asText(item i of bcList) starts with bcWant then return i
        end repeat
    end repeat
    return 0
end titleIndex
"""

# Find a message bubble in the transcript (right of the sidebar), newest first: walk the window
# backwards; nested elements of one bubble (same or enclosing frame) count once.
BUBBLES = """
on matchesBubble(bcLabel, bcNeedles, bcExacts)
    repeat with bcN in bcNeedles
        if bcLabel contains (bcN as text) then return true
    end repeat
    if (count of bcExacts) > 0 then
        repeat with bcX in bcExacts
            set bcW to bcX as text
            if bcLabel is bcW or bcLabel starts with (bcW & ",") or bcLabel contains (", " & bcW & ",") or bcLabel ends with (", " & bcW) then return true
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

-- The message in the open chat, newest first, by the labels of everything on screen (one request).
-- A label has to contain the text (or an attachment word); with senders, one naming the sender
-- wins. K and J skip that many newer look-alikes. Returns the message's balloon.
on findBubble(bcTranscript, bcNeedles, bcExacts, bcSenders, bcSkipSender, bcSkipAny)
    set bcDescs to {}
    try
        tell application "System Events" to set bcDescs to description of every UI element of bcTranscript
    end try
    set bcSenderHits to 0
    set bcAnyHits to 0
    set bcAnyPick to 0
    repeat with i from (count of bcDescs) to 1 by -1
        set bcLabel to my asText(item i of bcDescs)
        if (length of bcLabel) > 5 then
            if my matchesBubble(bcLabel, bcNeedles, bcExacts) then
                set bcAnyHits to bcAnyHits + 1
                if bcAnyHits = bcSkipAny + 1 then set bcAnyPick to i
                if (count of bcSenders) = 0 and bcAnyPick > 0 then exit repeat
                if (count of bcSenders) > 0 then
                    if my mentionsAny(bcLabel, bcSenders) then
                        set bcSenderHits to bcSenderHits + 1
                        if bcSenderHits = bcSkipSender + 1 then return my balloonOf(my atPath(bcTranscript, {i}))
                    end if
                end if
            end if
        end if
    end repeat
    if bcAnyPick is 0 then return missing value
    return my balloonOf(my atPath(bcTranscript, {bcAnyPick}))
end findBubble

-- Which item on screen shows this text at this minute (argv H: " 1:37", " 13:37"): the
-- (bcSkip + 1)-th from the bottom, or 0. Self-chats show each message twice in the same minute,
-- and the server counts those copies into bcSkip like any newer look-alike.
on timedIndex(bcTranscript, bcNeedles, bcExacts, bcTimes, bcSkip)
    set bcDescs to {}
    try
        tell application "System Events" to set bcDescs to description of every UI element of bcTranscript
    end try
    set bcSeen to 0
    set bcOldest to 0
    repeat with i from (count of bcDescs) to 1 by -1
        set bcLabel to my asText(item i of bcDescs)
        if (length of bcLabel) > 5 then
            if my matchesBubble(bcLabel, bcNeedles, bcExacts) and my mentionsAny(bcLabel, bcTimes) then
                set bcSeen to bcSeen + 1
                set bcOldest to i
                if bcSeen = bcSkip + 1 then return i
            end if
        end if
    end repeat
    -- Fewer look-alikes from that minute than chat.db has (a self-chat may show a message once though
    -- chat.db keeps a sent and a received copy): the oldest one showing it.
    return bcOldest
end timedIndex

-- What the search saw on screen, for the log: items / text matches / text-and-minute matches.
on bubbleStats(bcTranscript, bcNeedles, bcExacts, bcTimes)
    set bcDescs to {}
    try
        tell application "System Events" to set bcDescs to description of every UI element of bcTranscript
    end try
    set bcHits to 0
    set bcTimed to 0
    repeat with bcD in bcDescs
        set bcLabel to my asText(contents of bcD)
        if (length of bcLabel) > 5 then
            if my matchesBubble(bcLabel, bcNeedles, bcExacts) then
                set bcHits to bcHits + 1
                if bcTimes is not {} then
                    if my mentionsAny(bcLabel, bcTimes) then set bcTimed to bcTimed + 1
                end if
            end if
        end if
    end repeat
    return "b" & ((count of bcDescs) as text) & "/" & (bcHits as text) & "/" & (bcTimed as text)
end bubbleStats

-- Scroll the open chat back to its newest messages (at most bcPages pages).
on transcriptEnd(bcTranscript, bcPages)
    set bcSeen to my listKey(bcTranscript)
    repeat bcPages times
        if not my pageList(bcTranscript, "AXScrollDownByPage") then exit repeat
        delay 0.3
        set bcNow to my listKey(bcTranscript)
        if bcNow is bcSeen then exit repeat
        set bcSeen to bcNow
    end repeat
end transcriptEnd

-- The message's balloon, or missing value. With the minute it was sent (bcTimes), it's matched by
-- text and minute, scrolling the chat up a page at a time (at most 6) until it shows up; bcUp says
-- how far. Otherwise (or if that finds nothing) the older way: text, sender and skips, on screen.
on locateBubble(bcTranscript, bcNeedles, bcExacts, bcSenders, bcSkipSender, bcSkipAny, bcTimes, bcSkipMinute)
    set bcUp to 0
    if bcTimes is not {} then
        set bcSeen to my listKey(bcTranscript)
        repeat 7 times
            set bcAt to my timedIndex(bcTranscript, bcNeedles, bcExacts, bcTimes, bcSkipMinute)
            if bcAt > 0 then
                my addTrail("t" & (bcUp as text))
                return my balloonOf(my atPath(bcTranscript, {bcAt}))
            end if
            if bcUp is 6 then exit repeat
            if not my pageList(bcTranscript, "AXScrollUpByPage") then exit repeat
            delay 0.4
            set bcNow to my listKey(bcTranscript)
            if bcNow is bcSeen then exit repeat
            set bcSeen to bcNow
            set bcUp to bcUp + 1
        end repeat
        if bcUp > 0 then
            my transcriptEnd(bcTranscript, bcUp + 2)
            set bcUp to 0
        end if
    end if
    set bcFound to my findBubble(bcTranscript, bcNeedles, bcExacts, bcSenders, bcSkipSender, bcSkipAny)
    if bcFound is not missing value then my addTrail("s")
    return bcFound
end locateBubble

-- A message row's first child is its balloon (id "Sticker" on macOS 26), which carries the
-- tapback and Reply shortcuts; fall back to the row itself.
on balloonOf(bcRow)
    if bcRow is missing value then return missing value
    set bcKids to {}
    try
        tell application "System Events" to set bcKids to UI elements of bcRow
    end try
    -- The child (or grandchild) that carries the Reply and tapback shortcuts: rows with a thread
    -- line or a "2 Replies" label have other children first (Harry's Mac: the old first-child pick
    -- had no tapbacks, and a right-click on the row's middle opened no menu).
    repeat with bcKid in bcKids
        if my hasAction(contents of bcKid, {"Reply"}) then return contents of bcKid
    end repeat
    repeat with bcKid in bcKids
        set bcGrand to {}
        try
            tell application "System Events" to set bcGrand to UI elements of (contents of bcKid)
        end try
        repeat with bcG in bcGrand
            if my hasAction(contents of bcG, {"Reply"}) then return contents of bcG
        end repeat
    end repeat
    if my hasAction(bcRow, {"Reply"}) then return bcRow
    set bcInner to my atPath(bcRow, {1})
    if bcInner is missing value then return bcRow
    return bcInner
end balloonOf
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

# Any emoji as a tapback (doctor --messages-emoji-picker on macOS 26.3): "Add Emoji as Tapback" opens
# the system emoji picker, an AXPopover in Messages' window whose search field takes the focus, with
# a table of sections, each an AXGrid (CPKCharactersSectionStaticView) holding every emoji of that
# section. The emoji is found by its label in those grids (argv E: the emoji, Y: its names) and
# pressed; nothing is typed.
EMOJI_PICKER = """
on tapbackFromPicker(bcBubble, bcEmojis, bcNames)
    if not my customAction(bcBubble, {"Add Emoji"}) then return "no-emoji-picker"
    set bcPopover to missing value
    repeat 8 times
        delay 0.25
        set bcPopover to my pickerPopover()
        if bcPopover is not missing value then exit repeat
    end repeat
    if bcPopover is missing value then
        my pressEscape()
        return "no-emoji-picker"
    end if
    set bcCell to my pickerCell(bcPopover, bcEmojis, bcNames)
    if bcCell is missing value then
        my pressEscape()
        my pressEscape()
        return "no-emoji-in-picker"
    end if
    my addTrail("cell:" & my roleOf(bcCell))
    try
        tell application "System Events" to perform action "AXPress" of bcCell
        return "chosen-picker"
    end try
    try
        tell application "System Events" to set selected of bcCell to true
    end try
    delay 0.2
    my pressEscape()
    return "no-emoji-press"
end tapbackFromPicker

-- The emoji picker: the popover holding the focused search field.
on pickerPopover()
    set bcField to missing value
    try
        tell application "System Events" to set bcField to value of attribute "AXFocusedUIElement" of process "Messages"
    end try
    if bcField is missing value then return missing value
    set bcParent to missing value
    try
        tell application "System Events" to set bcParent to value of attribute "AXParent" of bcField
    end try
    if bcParent is missing value then return missing value
    if my roleOf(bcParent) is "AXPopover" then return bcParent
    return missing value
end pickerPopover

-- The emoji's cell: in each section's grid, a child whose label is the emoji or one of its names.
on pickerCell(bcPopover, bcEmojis, bcNames)
    set bcTable to my atPath(bcPopover, {1, 1})
    if bcTable is missing value then return missing value
    set bcRows to 0
    try
        tell application "System Events" to set bcRows to count of UI elements of bcTable
    end try
    set bcGrids to 0
    repeat with r from 1 to bcRows
        set bcGrid to my atPath(bcTable, {r, 1, 1})
        if bcGrid is not missing value then
            if my roleOf(bcGrid) is "AXGrid" then
                set bcGrids to bcGrids + 1
                set bcDescs to {}
                try
                    tell application "System Events" to set bcDescs to description of every UI element of bcGrid
                end try
                repeat with i from 1 to count of bcDescs
                    set bcLabel to my asText(item i of bcDescs)
                    if my labelIsEmoji(bcLabel, bcEmojis, bcNames) then
                        my addTrail("grid" & (r as text) & "/" & (i as text))
                        return my atPath(bcGrid, {i})
                    end if
                end repeat
            end if
        end if
    end repeat
    my addTrail("grids" & (bcGrids as text))
    return missing value
end pickerCell

on labelIsEmoji(bcLabel, bcEmojis, bcNames)
    if bcLabel is "" then return false
    repeat with bcE in bcEmojis
        set bcChar to contents of bcE
        if bcChar is not "" then
            considering case
                if bcLabel is bcChar or bcLabel starts with (bcChar & ",") or bcLabel starts with (bcChar & " ") then return true
            end considering
        end if
    end repeat
    repeat with bcN in bcNames
        set bcName to contents of bcN
        if bcName is not "" and bcLabel is bcName then return true
    end repeat
    return false
end labelIsEmoji
"""

# The reply script uses AppKit (AppleScriptObjC) for the clipboard, so it starts with these.
USE_APPKIT = """
use AppleScript version "2.4"
use framework "AppKit"
use scripting additions
"""

# Starting a reply (the reply script and the doctor's reply test).
MENUS = """
on findMenu(bcEl, bcWin)
    set bcMenu to missing value
    try
        tell application "System Events" to set bcMenu to menu 1 of bcEl
    end try
    if bcMenu is not missing value then return {bcMenu, "element"}
    try
        tell application "System Events" to set bcMenu to first UI element of process "Messages" whose role is "AXMenu"
    end try
    if bcMenu is not missing value then return {bcMenu, "app"}
    try
        tell application "System Events" to set bcMenu to menu 1 of bcWin
    end try
    if bcMenu is not missing value then return {bcMenu, "window"}
    return {missing value, "none"}
end findMenu

on topRoles()
    set bcRoles to {}
    try
        tell application "System Events" to set bcRoles to role of every UI element of process "Messages"
    end try
    return my cleanNames(bcRoles)
end topRoles

-- The focused text field's placeholder ("iMessage"; replying changes it), or "-".
on hintNow()
    set bcField to my focusedTextField()
    if bcField is missing value then return "-"
    return my attrText(bcField, "AXPlaceholderValue")
end hintNow

-- Which text field has the focus (role and id), or "-".
on focusKey()
    set bcField to my focusedTextField()
    if bcField is missing value then return "-"
    return my roleOf(bcField) & "|" & my attrText(bcField, "AXIdentifier")
end focusKey

-- Messages is replying: the text field's placeholder changed, or another text field has the focus.
-- (An inner group of the entry area changing isn't enough: on macOS 26.3 that came with a regular text.)
on stillReplying(bcHintBefore, bcFocusBefore)
    set bcHint to my hintNow()
    if bcHint is not "-" and bcHint is not "" and bcHint is not bcHintBefore then return true
    set bcKey to my focusKey()
    return bcKey is not "-" and bcKey is not bcFocusBefore
end stillReplying

on replyEngaged(bcHintBefore, bcFocusBefore)
    repeat 6 times
        if my stillReplying(bcHintBefore, bcFocusBefore) then return true
        delay 0.25
    end repeat
    return false
end replyEngaged

-- The message's right-click menu (AXShowMenu, with Messages in front), then its Reply item.
on replyFromMenu(bcBalloon, bcWin)
    try
        tell application "System Events" to perform action "AXShowMenu" of bcBalloon
    end try
    delay 0.7
    set bcFound to my findMenu(bcBalloon, bcWin)
    set bcMenu to item 1 of bcFound
    if bcMenu is missing value then return false
    set bcTitles to {}
    try
        tell application "System Events" to set bcTitles to name of every menu item of bcMenu
    end try
    repeat with i from 1 to count of bcTitles
        if (my asText(item i of bcTitles)) starts with "Reply" then
            try
                tell application "System Events" to click menu item i of bcMenu
                return true
            end try
        end if
    end repeat
    my pressEscape()
    return false
end replyFromMenu

-- A Reply command in the menu bar, if Messages has one: "bar<n>:<its title>" when chosen, else
-- "bar-disabled", "bar-failed" or "bar-none".
on replyFromMenuBar()
    set bcBarCount to 0
    try
        tell application "System Events" to set bcBarCount to count of menu bar items of menu bar 1 of process "Messages"
    end try
    repeat with bcBar from 2 to bcBarCount
        set bcTitles to {}
        try
            tell application "System Events" to set bcTitles to name of every menu item of menu 1 of menu bar item bcBar of menu bar 1 of process "Messages"
        end try
        repeat with i from 1 to count of bcTitles
            if (my asText(item i of bcTitles)) starts with "Reply" then
                set bcEnabled to false
                try
                    tell application "System Events" to set bcEnabled to enabled of menu item i of menu 1 of menu bar item bcBar of menu bar 1 of process "Messages"
                end try
                if bcEnabled is not true then return "bar-disabled"
                try
                    tell application "System Events" to click menu item i of menu 1 of menu bar item bcBar of menu bar 1 of process "Messages"
                    return "bar" & (bcBar as text) & ":" & my asText(item i of bcTitles)
                end try
                return "bar-failed"
            end if
        end repeat
    end repeat
    return "bar-none"
end replyFromMenuBar

-- Make this message the one Messages acts on: focus its row and select it (where Messages lets
-- that be set), so the menu bar's Reply answers it rather than the newest message.
on aimAt(bcBubble)
    set bcRow to missing value
    try
        tell application "System Events" to set bcRow to value of attribute "AXParent" of bcBubble
    end try
    if bcRow is not missing value then
        try
            tell application "System Events" to set focused of bcRow to true
        end try
        try
            tell application "System Events" to set selected of bcRow to true
        end try
    end if
    try
        tell application "System Events" to set focused of bcBubble to true
    end try
    delay 0.3
end aimAt

-- Put Messages in reply mode for this message. On macOS 26.3 only the menu bar's Reply does it
-- (the placeholder turns "Reply"; the message's "Reply…" action and its right-click menu don't), so
-- that comes first, aimed at the message; the other two stay as fallbacks. Counts only when the
-- placeholder or the focused field changes. The way that worked, or "".
on startReply(bcBubble, bcWin, bcHintBefore, bcFocusBefore)
    my aimAt(bcBubble)
    set bcBar to my replyFromMenuBar()
    if bcBar is not in {"bar-none", "bar-disabled", "bar-failed"} then
        delay 0.5
        if my replyEngaged(bcHintBefore, bcFocusBefore) then return "menubar " & bcBar
    end if
    my pressEscape()
    if my customAction(bcBubble, {"Reply"}) then
        delay 0.5
        if my replyEngaged(bcHintBefore, bcFocusBefore) then return "action"
    end if
    my pressEscape()
    if my replyFromMenu(bcBubble, bcWin) then
        delay 0.5
        if my replyEngaged(bcHintBefore, bcFocusBefore) then return "menu"
    end if
    my pressEscape()
    return ""
end startReply
"""

CLIPBOARD = """
on setClip(bcText)
    try
        set the clipboard to bcText
    end try
end setClip

-- Everything on the clipboard (every item, every kind: text, images, files…), copied.
on clipSave()
    set bcSaved to current application's NSMutableArray's array()
    set bcItems to {}
    try
        set bcItems to (current application's NSPasteboard's generalPasteboard()'s pasteboardItems()) as list
    end try
    repeat with bcItem in bcItems
        set bcCopy to current application's NSPasteboardItem's alloc()'s init()
        set bcTypes to {}
        try
            set bcTypes to ((contents of bcItem)'s types()) as list
        end try
        repeat with bcType in bcTypes
            set bcData to missing value
            try
                set bcData to (contents of bcItem)'s dataForType:(contents of bcType)
            end try
            if bcData is not missing value then bcCopy's setData:bcData forType:(contents of bcType)
        end repeat
        bcSaved's addObject:bcCopy
    end repeat
    return bcSaved
end clipSave

on clipRestore(bcSaved)
    try
        set bcBoard to current application's NSPasteboard's generalPasteboard()
        bcBoard's clearContents()
        if (bcSaved's |count|()) > 0 then bcBoard's writeObjects:bcSaved
    end try
end clipRestore
"""

# --------------------------------------------------------------------------- mark read

MARK_READ_ACT = """
on act(argv)
    set bcLooseNames to my tagged(argv, "L")
    set bcStrictNames to my tagged(argv, "N")
    set bcState to my messagesState()
    if bcState is not "ok" then return bcState
    tell application "System Events" to set bcWin to window 1 of process "Messages"
    set bcList to my spotList(bcWin)
    if bcList is missing value then return "no-list"
    set bcFound to my findRowScrolling(bcList, bcStrictNames, bcLooseNames, 4)
    set bcAt to item 1 of bcFound
    set bcTrail to "row" & (bcAt as text) & "/" & ((item 2 of bcFound) as text)
    if bcAt is 0 then
        my listHome(bcList, 6)
        return "row-not-found"
    end if
    set bcRowEl to my atPath(bcList, {bcAt})
    set bcAnswer to "no-mark-item"
    if my customAction(bcRowEl, {"Mark as Read"}) then
        set bcAnswer to "ok-action"
    else if my hasAction(bcRowEl, {"Mark as Unread"}) then
        set bcAnswer to "already-read"
    end if
    if (item 2 of bcFound) > 0 then my listHome(bcList, 6)
    return bcAnswer
end act
"""

# --------------------------------------------------------------------------- mark unread
# The row's own "Mark as Unread" action, like Mark read (works with Messages in the background).

MARK_UNREAD_ACT = """
on act(argv)
    set bcLooseNames to my tagged(argv, "L")
    set bcStrictNames to my tagged(argv, "N")
    set bcState to my messagesState()
    if bcState is not "ok" then return bcState
    tell application "System Events" to set bcWin to window 1 of process "Messages"
    set bcList to my spotList(bcWin)
    if bcList is missing value then return "no-list"
    set bcFound to my findRowScrolling(bcList, bcStrictNames, bcLooseNames, 4)
    set bcAt to item 1 of bcFound
    set bcTrail to "row" & (bcAt as text) & "/" & ((item 2 of bcFound) as text)
    if bcAt is 0 then
        my listHome(bcList, 6)
        return "row-not-found"
    end if
    set bcRowEl to my atPath(bcList, {bcAt})
    set bcAnswer to "no-mark-item"
    if my customAction(bcRowEl, {"Mark as Unread"}) then
        set bcAnswer to "ok-action"
    else if my hasAction(bcRowEl, {"Mark as Read"}) then
        set bcAnswer to "already-unread"
    end if
    if (item 2 of bcFound) > 0 then my listHome(bcList, 6)
    return bcAnswer
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
        set bcList to my spotList(bcWin)
        if bcList is missing value then return "no-list"
        set bcOpened to my openChat(bcWin, bcList, bcStrictNames, bcLooseNames, my tagged(argv, "U"))
        if bcOpened is not "ok" then return bcOpened
        set bcTranscript to my spotTranscript(bcWin)
        if bcTranscript is missing value then
            delay 0.8
            set bcTranscript to my spotTranscript(bcWin)
        end if
        if bcTranscript is missing value then return "no-transcript"
        set bcTimes to my tagged(argv, "H")
        set bcSkipMinute to my taggedNumber(argv, "Q")
        repeat 4 times
            if (my listKey(bcTranscript)) is not "0" then exit repeat
            delay 0.5
        end repeat
        set bcBubble to my locateBubble(bcTranscript, bcNeedles, bcExacts, bcSenders, bcSkipSender, bcSkipAny, bcTimes, bcSkipMinute)
        if bcBubble is missing value then
            my addTrail(my bubbleStats(bcTranscript, bcNeedles, bcExacts, bcTimes))
            return "bubble-not-found"
        end if
    on error bcErrMsg number bcErrNum
        return "error:" & (bcErrNum as text)
    end try
    set bcExtra to "quick" & tab & my emojiActions(bcBubble)
    set bcAnswer to "no-tapback-item"
    if my customAction(bcBubble, bcSynonyms) then set bcAnswer to "chosen-action"
    set bcPicked to my tagged(argv, "E")
    if bcAnswer is "no-tapback-item" and bcPicked is not {} then
        -- Not one of Messages' quick emoji: its emoji picker (it shows with Messages in front).
        my bringForward()
        set bcAnswer to my tapbackFromPicker(bcBubble, bcPicked, my tagged(argv, "Y"))
    end if
    if bcUp > 0 then
        delay 0.3
        my transcriptEnd(bcTranscript, bcUp + 2)
    end if
    return bcAnswer
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
        set bcList to my spotList(bcWin)
        if bcList is missing value then return "no-list"
        set bcOpened to my openChat(bcWin, bcList, bcStrictNames, bcLooseNames, my tagged(argv, "U"))
        if bcOpened is not "ok" then return bcOpened
        set bcTranscript to my spotTranscript(bcWin)
        if bcTranscript is missing value then
            delay 0.8
            set bcTranscript to my spotTranscript(bcWin)
        end if
        if bcTranscript is missing value then return "no-transcript"
        set bcTimes to my tagged(argv, "H")
        set bcSkipMinute to my taggedNumber(argv, "Q")
        repeat 4 times
            if (my listKey(bcTranscript)) is not "0" then exit repeat
            delay 0.5
        end repeat
        set bcBubble to my locateBubble(bcTranscript, bcNeedles, bcExacts, bcSenders, bcSkipSender, bcSkipAny, bcTimes, bcSkipMinute)
        if bcBubble is missing value then
            my addTrail(my bubbleStats(bcTranscript, bcNeedles, bcExacts, bcTimes))
            return "bubble-not-found"
        end if
        my bringForward()
        set bcHintBefore to my hintNow()
        set bcFocusBefore to my focusKey()
        if not my hasAction(bcBubble, {"Reply"}) then return "no-reply-item"
        set bcStart to my startReply(bcBubble, bcWin, bcHintBefore, bcFocusBefore)
        if bcStart is "" then return "no-reply-mode"
        my addTrail("r:" & bcStart)
    on error bcErrMsg number bcErrNum
        return "error:" & (bcErrNum as text)
    end try
    return my typeReply(bcReply, bcHintBefore, bcFocusBefore)
end act
"""

# Putting the reply in and sending it, shared by both ways into reply mode (the menu bar's "Reply to
# Last Message" for the newest message; the message's own right-click menu for older ones).
REPLY_TYPING = """
-- Paste, as a person would: Messages keeps the reply attached to pasted or typed text, but not to
-- text set as the field's value (that went out as a regular text on macOS 26.3). Everything on the
-- clipboard is saved first and put back right after. Return only while the field holds exactly the
-- reply and Messages is still replying.
on typeReply(bcReply, bcHintBefore, bcFocusBefore)
    try
        set bcBox to my focusedTextField()
        if bcBox is missing value then
            my pressEscape()
            return "no-reply-field"
        end if
        if (my fieldText(bcBox)) is not "" then
            my pressEscape()
            return "field-busy"
        end if
    on error bcErrMsg number bcErrNum
        return "error:" & (bcErrNum as text)
    end try
    set bcHow to ""
    set bcSaved to my clipSave()
    my setClip(bcReply)
    try
        tell application "System Events" to key code 9 using command down
    end try
    set bcFocused to missing value
    repeat 10 times
        delay 0.15
        set bcFocused to my focusedTextField()
        if bcFocused is not missing value then
            considering case
                if (my fieldText(bcFocused)) is bcReply then set bcHow to "paste"
            end considering
        end if
        if bcHow is not "" then exit repeat
    end repeat
    my clipRestore(bcSaved)
    if bcHow is "" then
        if bcFocused is missing value then return "typed-unknown"
        if (my fieldText(bcFocused)) is "" then
            my pressEscape()
            return "nothing-typed"
        end if
        return "typed-unknown"
    end if
    -- Still replying? If Messages let go of the message being answered, take the text back out
    -- rather than send it as a regular text.
    if not my stillReplying(bcHintBefore, bcFocusBefore) then
        set bcBox to bcFocused
        try
            tell application "System Events" to set value of bcBox to ""
        end try
        my pressEscape()
        return "reply-mode-lost"
    end if
    my bringForward()
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
end typeReply
"""

# --------------------------------------------------------------------------- reply to an older message
# "Reply to Last Message" only answers the newest message, and accessibility can't open a message's
# own menu on macOS 26.3 (its "Reply…" action and AXShowMenu do nothing). So: this script finds the
# message and says where it is on screen (aim:x,y; Messages stays in front), the server right-clicks
# there (server/mouse.py), and REPLY_FINISH_ACT chooses Reply… in the menu that opened and sends.

REPLY_AIM_ACT = """
on act(argv)
    set bcLooseNames to my tagged(argv, "L")
    set bcStrictNames to my tagged(argv, "N")
    set bcNeedles to my tagged(argv, "T")
    set bcExacts to my tagged(argv, "W")
    set bcSenders to my tagged(argv, "S")
    set bcSkipSender to my taggedNumber(argv, "K")
    set bcSkipAny to my taggedNumber(argv, "J")
    try
        set bcState to my messagesState()
        if bcState is not "ok" then return bcState
        tell application "System Events" to set bcWin to window 1 of process "Messages"
        set bcList to my spotList(bcWin)
        if bcList is missing value then return "no-list"
        set bcOpened to my openChat(bcWin, bcList, bcStrictNames, bcLooseNames, my tagged(argv, "U"))
        if bcOpened is not "ok" then return bcOpened
        set bcTranscript to my spotTranscript(bcWin)
        if bcTranscript is missing value then
            delay 0.8
            set bcTranscript to my spotTranscript(bcWin)
        end if
        if bcTranscript is missing value then return "no-transcript"
        set bcTimes to my tagged(argv, "H")
        set bcSkipMinute to my taggedNumber(argv, "Q")
        repeat 4 times
            if (my listKey(bcTranscript)) is not "0" then exit repeat
            delay 0.5
        end repeat
        set bcBubble to my locateBubble(bcTranscript, bcNeedles, bcExacts, bcSenders, bcSkipSender, bcSkipAny, bcTimes, bcSkipMinute)
        if bcBubble is missing value then
            my addTrail(my bubbleStats(bcTranscript, bcNeedles, bcExacts, bcTimes))
            return "bubble-not-found"
        end if
        if not my hasAction(bcBubble, {"Reply"}) then return "no-reply-item"
        my bringForward()
        try
            tell application "System Events" to perform action "AXScrollToVisible" of bcBubble
        end try
        delay 0.4
        set bcPoint to my visiblePoint(bcBubble, bcTranscript)
        if bcPoint is missing value then return "not-visible"
        set bcField to my searchId(my spotEntry(bcWin), "messageBodyField")
        set bcHint to my attrText(bcField, "AXPlaceholderValue")
        set bcKeepFront to true
        set bcExtra to "front" & tab & bcFrontWas & linefeed & "hint" & tab & bcHint
        return "aim:" & ((item 1 of bcPoint) as text) & "," & ((item 2 of bcPoint) as text)
    on error bcErrMsg number bcErrNum
        return "error:" & (bcErrNum as text)
    end try
end act

-- The middle of the part of an element that's inside the view, in screen points, or missing value.
on visiblePoint(bcEl, bcView)
    set bcF to my frameOf(bcEl)
    if bcF is missing value then return missing value
    set bcLeft to item 1 of bcF
    set bcTop to item 2 of bcF
    set bcRight to bcLeft + (item 3 of bcF)
    set bcBottom to bcTop + (item 4 of bcF)
    set bcV to my frameOf(bcView)
    if bcV is not missing value then
        if (item 1 of bcV) > bcLeft then set bcLeft to item 1 of bcV
        if (item 2 of bcV) > bcTop then set bcTop to item 2 of bcV
        if (item 1 of bcV) + (item 3 of bcV) < bcRight then set bcRight to (item 1 of bcV) + (item 3 of bcV)
        if (item 2 of bcV) + (item 4 of bcV) < bcBottom then set bcBottom to (item 2 of bcV) + (item 4 of bcV)
    end if
    if bcBottom - bcTop < 6 or bcRight - bcLeft < 6 then return missing value
    return {round ((bcLeft + bcRight) / 2), round ((bcTop + bcBottom) / 2)}
end visiblePoint
"""

REPLY_FINISH_ACT = """
on act(argv)
    set bcTexts to my tagged(argv, "M")
    if bcTexts is {} then return "no-text"
    set bcReply to item 1 of bcTexts
    set bcFronts to my tagged(argv, "F")
    if bcFronts is not {} then set bcRestoreTo to item 1 of bcFronts
    set bcHintBefore to "iMessage"
    set bcHints to my tagged(argv, "B")
    if bcHints is not {} then set bcHintBefore to item 1 of bcHints
    set bcFocusBefore to "AXTextField|messageBodyField"
    try
        tell application "System Events" to set bcWin to window 1 of process "Messages"
        set bcMenu to missing value
        repeat 10 times
            set bcMenu to item 1 of (my findMenu(bcWin, bcWin))
            if bcMenu is not missing value then exit repeat
            delay 0.2
        end repeat
        if bcMenu is missing value then
            my addTrail("top:" & my joinWith(my topRoles(), ",") & " w" & ((my windowTotal()) as text))
            return "no-menu"
        end if
        set bcTitles to {}
        try
            tell application "System Events" to set bcTitles to name of every menu item of bcMenu
        end try
        set bcAt to 0
        repeat with i from 1 to count of bcTitles
            if bcAt is 0 and (my asText(item i of bcTitles)) starts with "Reply" then set bcAt to i
        end repeat
        if bcAt is 0 then
            my pressEscape()
            return "no-reply-item"
        end if
        tell application "System Events" to click menu item bcAt of bcMenu
        if not my replyEngaged(bcHintBefore, bcFocusBefore) then
            my pressEscape()
            return "no-reply-mode"
        end if
        my addTrail("r:right-click")
    on error bcErrMsg number bcErrNum
        return "error:" & (bcErrNum as text)
    end try
    return my typeReply(bcReply, bcHintBefore, bcFocusBefore)
end act
"""

# --------------------------------------------------------------------------- a link with its preview
# AppleScript sends are plain text, so links go out without a preview. A message that is only a link
# is pasted into the chat's own text field instead (Messages loads the preview while it sits there)
# and sent with Return. Return is only pressed while the field holds our link (or the preview card
# Messages turned it into); the field must be empty before.

COMPOSE_ACT = """
on act(argv)
    set bcLooseNames to my tagged(argv, "L")
    set bcStrictNames to my tagged(argv, "N")
    set bcTexts to my tagged(argv, "M")
    if bcTexts is {} then return "no-text"
    set bcText to item 1 of bcTexts
    try
        set bcState to my messagesState()
        if bcState is not "ok" then return bcState
        tell application "System Events" to set bcWin to window 1 of process "Messages"
        set bcList to my spotList(bcWin)
        if bcList is missing value then return "no-list"
        set bcOpened to my openChat(bcWin, bcList, bcStrictNames, bcLooseNames, my tagged(argv, "U"))
        if bcOpened is not "ok" then return bcOpened
        my bringForward()
        set bcEntry to my spotEntry(bcWin)
        if bcEntry is missing value then return "no-field"
        set bcBox to my searchId(bcEntry, "messageBodyField")
        if bcBox is missing value then return "no-field"
        try
            tell application "System Events" to set focused of bcBox to true
        end try
        delay 0.2
        set bcFocused to my focusedTextField()
        if bcFocused is missing value then return "no-field"
        if my attrText(bcFocused, "AXIdentifier") is not "messageBodyField" then return "no-field"
        if (my fieldText(bcFocused)) is not "" then return "field-busy"
    on error bcErrMsg number bcErrNum
        return "error:" & (bcErrNum as text)
    end try
    set bcSaved to my clipSave()
    my setClip(bcText)
    try
        tell application "System Events" to key code 9 using command down
    end try
    set bcHow to ""
    repeat 10 times
        delay 0.15
        if (my fieldText(bcFocused)) is not "" then
            set bcHow to "paste"
            exit repeat
        end if
    end repeat
    my clipRestore(bcSaved)
    if bcHow is "" then return "nothing-typed"
    -- Messages loads the link's preview while the link sits in the field.
    delay 2.5
    my bringForward()
    set bcFocused to my focusedTextField()
    if bcFocused is missing value then return "typed-not-sent"
    if my attrText(bcFocused, "AXIdentifier") is not "messageBodyField" then return "typed-not-sent"
    set bcNow to my fieldText(bcFocused)
    set bcOurs to false
    if bcNow is not "" then
        if bcNow contains bcText or bcNow contains (character id 65532) then set bcOurs to true
    end if
    if not bcOurs then return "typed-not-sent"
    tell application "System Events" to key code 36
    delay 0.8
    if (my fieldText(bcFocused)) is "" then return "sent-" & bcHow
    return "returned-unconfirmed"
end act
"""

# --------------------------------------------------------------------------- reply test (doctor)
# Run only from `doctor --messages-reply-test`, after it checked that the user's own chat is open
# (the script checks the title again). W: names the way to start replying; if Messages switches
# into reply mode, the M: text is pasted and sent to the user's own chat. Prints placeholders,
# focus keys and counts, never message text.

REPLY_TEST_MAIN = """
property bcBudget : 320
property bcTrail : ""
property bcUp : 0
property bcMembers : {}

on run argv
    set bcRunning to false
    tell application "System Events" to set bcRunning to (exists process "Messages")
    if bcRunning is not true then return "rt" & tab & "not-running"
    set bcWinCount to 0
    tell application "System Events" to set bcWinCount to count of windows of process "Messages"
    if bcWinCount is 0 then return "rt" & tab & "no-window"
    tell application "System Events" to set bcWin to window 1 of process "Messages"
    if not my titleMatches(bcWin, my tagged(argv, "N"), {}) then return "rt" & tab & "not-your-chat"
    set bcMethod to ""
    set bcMethods to my tagged(argv, "W")
    if bcMethods is not {} then set bcMethod to item 1 of bcMethods
    set bcTexts to my tagged(argv, "M")
    set bcPrevApp to my frontBundle()
    my bringForward()
    my pressEscape()
    set bcOut to {"rt_method" & tab & bcMethod}
    set bcBalloon to my newestBalloon(bcWin)
    if bcBalloon is missing value then
        my restoreFront(bcPrevApp)
        return my joinWith(bcOut & {"rt" & tab & "no-message"}, linefeed)
    end if
    set bcEntry to my spotEntry(bcWin)
    set bcHintBefore to my hintNow()
    set bcFocusBefore to my focusKey()
    set bcMarksBefore to my replyMarks(bcEntry)
    set bcDid to "no"
    if bcMethod is "action" then
        if my customAction(bcBalloon, {"Reply"}) then set bcDid to "yes"
    else if bcMethod is "menu" then
        if my replyFromMenu(bcBalloon, bcWin) then set bcDid to "yes"
    else if bcMethod is "menubar" then
        set bcDid to my replyFromMenuBar()
    end if
    delay 1.2
    set bcHintAfter to my hintNow()
    set bcFocusAfter to my focusKey()
    set bcMarksAfter to my replyMarks(bcEntry)
    set bcOut to bcOut & {"rt_did" & tab & bcDid, "rt_hint" & tab & bcHintBefore & tab & bcHintAfter}
    set bcOut to bcOut & {"rt_focus" & tab & bcFocusBefore & tab & bcFocusAfter}
    set bcOut to bcOut & {"rt_marks" & tab & (bcMarksBefore as text) & tab & (bcMarksAfter as text)}
    set bcOut to bcOut & {"rt_top" & tab & my joinWith(my topRoles(), ",")}
    set bcEngaged to my stillReplying(bcHintBefore, bcFocusBefore)
    set bcOut to bcOut & {"rt_engaged" & tab & (bcEngaged as text)}
    if (bcEngaged or bcMarksAfter > bcMarksBefore) and bcTexts is not {} then
        set bcText to item 1 of bcTexts
        set bcSaved to my clipSave()
        my setClip(bcText)
        try
            tell application "System Events" to key code 9 using command down
        end try
        delay 0.6
        my clipRestore(bcSaved)
        set bcField to my focusedTextField()
        set bcReady to false
        if bcField is not missing value then
            considering case
                if (my fieldText(bcField)) is bcText then set bcReady to true
            end considering
        end if
        set bcOut to bcOut & {"rt_hint_with_text" & tab & my hintNow()}
        if bcReady then
            tell application "System Events" to key code 36
            delay 1.0
            set bcOut to bcOut & {"rt_sent" & tab & "yes"}
        else
            set bcOut to bcOut & {"rt_sent" & tab & "no"}
        end if
    end if
    my pressEscape()
    my restoreFront(bcPrevApp)
    return my joinWith(bcOut, linefeed)
end run
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
property bcBudget : 320
property bcTrail : ""
property bcMembers : {}

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
        set bcMode to ""
        if (count of argv) > 0 then set bcMode to item 1 of argv
        set bcFront to false
        if (count of argv) > 1 then
            if item 2 of argv is "front" then set bcFront to true
        end if
        if bcMode is "tree" then
            set bcReport to bcReport & {my walk(bcWin, 0, 16)}
        else if bcMode is "title" then
            set bcReport to bcReport & {"title_text" & tab & my titleText(bcWin)}
        else if bcMode is "open" then
            set bcMembers to my tagged(argv, "G")
            set bcPrevApp to my frontBundle()
            set bcList to my spotList(bcWin)
            if bcList is missing value then
                set bcReport to bcReport & {"open" & tab & "no-list" & tab & ""}
            else
                set bcOpened to my openChat(bcWin, bcList, my tagged(argv, "N"), my tagged(argv, "L"), my tagged(argv, "U"))
                set bcReport to bcReport & {"open" & tab & bcOpened & tab & bcTrail}
            end if
            my restoreFront(bcPrevApp)
        else if bcMode is "try-tapback" then
            set bcReport to bcReport & my tryTapback(bcWin, bcFront, my tagged(argv, "R"))
        else if bcMode is "try-reply" then
            set bcReport to bcReport & my tryReply(bcWin, bcFront)
        else if bcMode is "emoji-picker" then
            set bcReport to bcReport & my emojiPickerProbe(bcWin)
        else
            set bcReport to bcReport & my probeAll(bcWin)
        end if
    end if
    return my joinWith(bcReport, linefeed)
end run

-- The spots found by the tree walk on macOS 26.3, as child-index paths from the window. Each is
-- checked by its accessibility id before use, so a different layout reports "missing" instead of
-- poking at the wrong element.
on probeAll(bcWin)
    set bcOut to {}
    set bcList to my spotList(bcWin)
    set bcListId to my attrText(bcList, "AXIdentifier")
    set bcOut to bcOut & {"spot" & tab & "list" & tab & bcListId}
    set bcTranscript to my spotTranscript(bcWin)
    set bcTransId to my attrText(bcTranscript, "AXIdentifier")
    set bcOut to bcOut & {"spot" & tab & "transcript" & tab & bcTransId}
    set bcTitle to my spotTitle(bcWin)
    set bcTitleId to my attrText(bcTitle, "AXIdentifier")
    set bcOut to bcOut & {"spot" & tab & "title" & tab & bcTitleId}
    if bcListId is "ConversationList" then set bcOut to bcOut & my sidebarReport(bcList)
    if bcTransId is "TranscriptCollectionView" then set bcOut to bcOut & my bubbleReport(bcTranscript, bcWin)
    return bcOut
end probeAll

-- A label's shape without its words: the length of each ", "-separated part, with a few
-- status words and clock times named.
on descShape(bcDesc)
    if bcDesc is "" then return ""
    set bcSaved to AppleScript's text item delimiters
    set AppleScript's text item delimiters to ", "
    set bcParts to text items of bcDesc
    set AppleScript's text item delimiters to bcSaved
    set bcTags to {}
    repeat with bcP in bcParts
        set bcPart to contents of bcP
        set bcTag to (length of bcPart) as text
        if bcPart is in {"Pinned", "Unread", "Read", "Delivered", "Sent", "Edited", "Muted", "Today", "Yesterday"} then set bcTag to bcPart
        if bcPart contains ":" and (bcPart ends with "AM" or bcPart ends with "PM") and (length of bcPart) < 12 then set bcTag to "time"
        set bcTags to bcTags & {bcTag}
    end repeat
    return my joinWith(bcTags, ",")
end descShape

on windowCount()
    set bcN to 0
    try
        tell application "System Events" to set bcN to count of windows of process "Messages"
    end try
    return bcN
end windowCount

on sidebarReport(bcList)
    set bcOut to {}
    set bcRoles to {}
    set bcDescs to {}
    set bcSels to {}
    try
        tell application "System Events" to set bcRoles to role of every UI element of bcList
    end try
    try
        tell application "System Events" to set bcDescs to description of every UI element of bcList
    end try
    try
        tell application "System Events" to set bcSels to value of attribute "AXSelected" of every UI element of bcList
    end try
    set bcSelKids to {}
    try
        tell application "System Events" to set bcSelKids to value of attribute "AXSelectedChildren" of bcList
    end try
    set bcCount to count of bcRoles
    set bcOut to bcOut & {"rows" & tab & (bcCount as text) & tab & ((count of bcDescs) as text) & tab & ((count of bcSels) as text) & tab & ((count of bcSelKids) as text)}
    set bcFewAt to 0
    set bcManyAt to 0
    repeat with i from 1 to bcCount
        set bcRole to my asText(item i of bcRoles)
        set bcDesc to ""
        if i is less than or equal to (count of bcDescs) then set bcDesc to my asText(item i of bcDescs)
        set bcSel to ""
        if i is less than or equal to (count of bcSels) then set bcSel to my asText(item i of bcSels)
        set bcShape to my descShape(bcDesc)
        set bcOut to bcOut & {"row" & tab & (i as text) & tab & bcRole & tab & ((length of bcDesc) as text) & tab & bcSel & tab & bcShape}
        if bcRole is "AXGenericElement" and bcDesc is not "" then
            set bcPartCount to count of my splitOn(bcShape, ",")
            if bcPartCount is less than 3 and bcFewAt is 0 then set bcFewAt to i
            if bcPartCount is greater than 2 and bcManyAt is 0 then set bcManyAt to i
        end if
    end repeat
    if bcFewAt > 0 then set bcOut to bcOut & {"row_actions" & tab & "short-label" & tab & my joinWith(my actionsOf(my atPath(bcList, {bcFewAt})), ",")}
    if bcManyAt > 0 then
        set bcRowEl to my atPath(bcList, {bcManyAt})
        set bcOut to bcOut & {"row_actions" & tab & "long-label" & tab & my joinWith(my actionsOf(bcRowEl), ",")}
        set bcSettable to missing value
        try
            tell application "System Events" to set bcSettable to settable of attribute "AXSelected" of bcRowEl
        end try
        set bcOut to bcOut & {"row_selected_settable" & tab & my asText(bcSettable)}
    end if
    return bcOut
end sidebarReport


-- Button labels under an element, a few levels deep (the tapback picker's buttons).
on collectButtons(bcRoot, bcDepth)
    set bcFound to {}
    if bcDepth > 7 or bcBudget is less than or equal to 0 then return bcFound
    set bcBudget to bcBudget - 1
    set bcKids to {}
    set bcRoles to {}
    set bcDescs to {}
    try
        tell application "System Events" to set bcKids to UI elements of bcRoot
    end try
    try
        tell application "System Events" to set bcRoles to role of every UI element of bcRoot
    end try
    try
        tell application "System Events" to set bcDescs to description of every UI element of bcRoot
    end try
    repeat with i from 1 to count of bcKids
        set bcRole to ""
        if i is less than or equal to (count of bcRoles) then set bcRole to my asText(item i of bcRoles)
        if bcRole is in {"AXButton", "AXRadioButton", "AXCheckBox", "AXMenuItem"} then
            if i is less than or equal to (count of bcDescs) then set bcFound to bcFound & {my asText(item i of bcDescs)}
        else
            set bcFound to bcFound & my collectButtons(item i of bcKids, bcDepth + 1)
        end if
    end repeat
    return bcFound
end collectButtons

on pickerReport(bcWin, bcWinsBefore)
    set bcOut to {}
    set bcWins to {}
    try
        tell application "System Events" to set bcWins to windows of process "Messages"
    end try
    repeat with i from 1 to count of bcWins
        set bcThisWin to item i of bcWins
        set bcId to my attrText(bcThisWin, "AXIdentifier")
        set bcSub to my attrText(bcThisWin, "AXSubrole")
        set bcOut to bcOut & {"window" & tab & (i as text) & tab & bcId & tab & bcSub}
        if bcId is not "SceneWindow" then
            set bcBudget to 80
            set bcOut to bcOut & {"picker_buttons" & tab & "window" & tab & my joinWith(my collectButtons(bcThisWin, 0), " | ")}
        end if
    end repeat
    set bcPop to missing value
    try
        tell application "System Events" to set bcPop to first UI element of bcWin whose role is "AXPopover"
    end try
    if bcPop is not missing value then
        set bcBudget to 80
        set bcOut to bcOut & {"picker_buttons" & tab & "popover" & tab & my joinWith(my collectButtons(bcPop, 0), " | ")}
    end if
    return bcOut
end pickerReport

-- The newest message: its actions, its right-click menu, and the tapback picker if the menu has
-- one. Nothing is chosen except the menu's own Tapback entry; Escape closes everything.
on bubbleReport(bcTranscript, bcWin)
    set bcOut to {}
    set bcDescs to {}
    try
        tell application "System Events" to set bcDescs to description of every UI element of bcTranscript
    end try
    set bcCount to count of bcDescs
    set bcOut to bcOut & {"bubbles" & tab & (bcCount as text)}
    set bcNewest to 0
    repeat with i from 1 to bcCount
        set bcDesc to my asText(item i of bcDescs)
        set bcOut to bcOut & {"bubble_row" & tab & (i as text) & tab & ((length of bcDesc) as text) & tab & my descShape(bcDesc)}
        if (length of bcDesc) > 20 then set bcNewest to i
    end repeat
    if bcNewest is 0 then return bcOut & {"bubble" & tab & "none"}
    set bcRow to my atPath(bcTranscript, {bcNewest})
    set bcBalloon to my atPath(bcRow, {1})
    set bcTextEl to my atPath(bcBalloon, {1})
    set bcOut to bcOut & {"bubble_actions" & tab & "row" & tab & my joinWith(my actionsOf(bcRow), ",") & tab & my attrText(bcRow, "AXIdentifier")}
    set bcOut to bcOut & {"bubble_actions" & tab & "balloon" & tab & my joinWith(my actionsOf(bcBalloon), ",") & tab & my attrText(bcBalloon, "AXIdentifier")}
    set bcOut to bcOut & {"bubble_actions" & tab & "text" & tab & my joinWith(my actionsOf(bcTextEl), ",") & tab & my attrText(bcTextEl, "AXIdentifier")}
    set bcPrevApp to my frontBundle()
    set bcOut to bcOut & {"top_before" & tab & my joinWith(my topRoles(), ",")}
    try
        tell application "System Events" to perform action "AXShowMenu" of bcBalloon
    end try
    delay 0.7
    set bcOut to bcOut & {"top_after" & tab & my joinWith(my topRoles(), ",")}
    set bcFound to my findMenu(bcBalloon, bcWin)
    set bcMenu to item 1 of bcFound
    set bcOut to bcOut & {"bubble_menu_at" & tab & (item 2 of bcFound)}
    if bcMenu is missing value then
        my pressEscape()
        my restoreFront(bcPrevApp)
        return bcOut & {"bubble_menu" & tab & "(none)"}
    end if
    set bcTitles to {}
    try
        tell application "System Events" to set bcTitles to name of every menu item of bcMenu
    end try
    set bcOut to bcOut & {"bubble_menu" & tab & my joinWith(my cleanNames(bcTitles), " | ")}
    set bcTapAt to 0
    repeat with i from 1 to count of bcTitles
        set bcT to my asText(item i of bcTitles)
        if bcTapAt is 0 and (bcT starts with "Tapback" or bcT starts with "React") then set bcTapAt to i
    end repeat
    if bcTapAt is 0 then
        my pressEscape()
        my restoreFront(bcPrevApp)
        return bcOut & {"tapback_item" & tab & "no"}
    end if
    set bcOut to bcOut & {"tapback_item" & tab & "yes"}
    set bcWinsBefore to my windowCount()
    try
        tell application "System Events" to click menu item bcTapAt of bcMenu
    end try
    delay 0.8
    set bcOut to bcOut & {"after_tapback" & tab & my joinWith(my topRoles(), ",") & tab & ((my windowCount()) as text) & tab & (bcWinsBefore as text)}
    set bcOut to bcOut & my pickerReport(bcWin, bcWinsBefore)
    my pressEscape()
    my restoreFront(bcPrevApp)
    return bcOut
end bubbleReport

on actionsOf(bcEl)
    set bcActs to {}
    try
        tell application "System Events" to set bcActs to name of every action of bcEl
    end try
    set bcClean to {}
    repeat with bcA in my cleanNames(bcActs)
        set bcClean to bcClean & {my oneLine(contents of bcA)}
    end repeat
    return bcClean
end actionsOf

-- Custom actions are named like "Name:Reply<newline>Target:...": keep each on one line, no commas.
on oneLine(bcText)
    set bcSaved to AppleScript's text item delimiters
    set AppleScript's text item delimiters to {linefeed, return, tab, ","}
    set bcBits to text items of bcText
    set AppleScript's text item delimiters to " "
    set bcFlat to bcBits as text
    set AppleScript's text item delimiters to bcSaved
    return bcFlat
end oneLine

-- A bounded walk, one level at a time (never "entire contents", which takes over a minute on a
-- big Messages window). Each level shows its first 5 and last 3 children, so both ends of long
-- lists (the oldest and newest messages) appear. Text is reported as lengths only.
on walk(bcEl, bcDepth, bcMaxDepth)
    if bcBudget is less than or equal to 0 then return ""
    set bcBudget to bcBudget - 1
    set bcRole to my roleOf(bcEl)
    if bcRole is "" then set bcRole to "?"
    set bcSub to my attrText(bcEl, "AXSubrole")
    set bcIdent to my attrText(bcEl, "AXIdentifier")
    set bcDescLen to length of (my attrText(bcEl, "AXDescription"))
    set bcValueLen to length of (my attrText(bcEl, "AXValue"))
    set bcKids to {}
    try
        tell application "System Events" to set bcKids to UI elements of bcEl
    end try
    set bcCount to count of bcKids
    set bcMenuFlag to "-"
    if bcCount is less than 3 then
        if (my actionsOf(bcEl)) contains "AXShowMenu" then set bcMenuFlag to "menu"
    end if
    set bcReport to {"tree" & tab & (bcDepth as text) & tab & bcRole & tab & bcSub & tab & (bcCount as text) & tab & bcIdent & tab & (bcDescLen as text) & tab & (bcValueLen as text) & tab & bcMenuFlag}
    if bcDepth is less than bcMaxDepth and bcCount is greater than 0 then
        repeat with i from 1 to bcCount
            if bcBudget is less than or equal to 0 then exit repeat
            if i is greater than 5 and i is less than or equal to (bcCount - 3) then
                if i is 6 then set bcReport to bcReport & {"skip" & tab & ((bcDepth + 1) as text) & tab & ((bcCount - 8) as text)}
            else
                set bcReport to bcReport & {my walk(item i of bcKids, bcDepth + 1, bcMaxDepth)}
            end if
        end repeat
    end if
    return my joinWith(bcReport, linefeed)
end walk

-- ---- live tries (doctor --messages-try; it checks first that your own chat is the open one)

on newestBalloon(bcWin)
    set bcTranscript to my spotTranscript(bcWin)
    if bcTranscript is missing value then return missing value
    set bcDescs to {}
    try
        tell application "System Events" to set bcDescs to description of every UI element of bcTranscript
    end try
    set bcNewest to 0
    repeat with i from 1 to count of bcDescs
        if (length of (my asText(item i of bcDescs))) > 20 then set bcNewest to i
    end repeat
    if bcNewest is 0 then return missing value
    return my balloonOf(my atPath(bcTranscript, {bcNewest}))
end newestBalloon

on tryTapback(bcWin, bcFront, bcSyns)
    set bcOut to {}
    set bcBalloon to my newestBalloon(bcWin)
    if bcBalloon is missing value then return {"try" & tab & "no-message"}
    set bcOut to bcOut & {"try_actions" & tab & my joinWith(my actionsOf(bcBalloon), ",")}
    set bcPrevApp to my frontBundle()
    if bcFront then my bringForward()
    set bcDid to my customAction(bcBalloon, bcSyns)
    delay 0.5
    if bcFront then my restoreFront(bcPrevApp)
    return bcOut & {"try" & tab & (bcDid as text)}
end tryTapback

-- doctor --messages-emoji-picker (own chat only; the doctor checked the title): what "Add Emoji as
-- Tapback" on the newest message opens: top-level elements, windows, picker-like processes, where
-- the focus went (its ancestors) and the tree around it. Roles, ids, counts and lengths only; then
-- Escape twice. Nothing is chosen.
on emojiPickerProbe(bcWin)
    set bcOut to {}
    set bcBalloon to my newestBalloon(bcWin)
    if bcBalloon is missing value then return {"ep" & tab & "no-message"}
    set bcPrevApp to my frontBundle()
    my bringForward()
    my pressEscape()
    set bcProcsBefore to my pickerProcesses()
    set bcWinsBefore to my windowCount()
    set bcOut to bcOut & {"ep_top" & tab & "before" & tab & my joinWith(my topRoles(), ",")}
    set bcOut to bcOut & {"ep_focus" & tab & "before" & tab & my focusInfo()}
    set bcDid to my customAction(bcBalloon, {"Add Emoji"})
    delay 1.5
    set bcOut to bcOut & {"ep_did" & tab & (bcDid as text)}
    set bcOut to bcOut & {"ep_top" & tab & "after" & tab & my joinWith(my topRoles(), ",")}
    set bcOut to bcOut & {"ep_focus" & tab & "after" & tab & my focusInfo()}
    set bcOut to bcOut & {"ep_windows" & tab & (bcWinsBefore as text) & tab & ((my windowCount()) as text)}
    set bcOut to bcOut & {"ep_procs" & tab & bcProcsBefore & tab & my pickerProcesses()}
    set bcOut to bcOut & {"ep_chain" & tab & my focusChain()}
    set bcNear to my focusAncestor(4)
    if bcNear is not missing value then
        set bcBudget to 150
        set bcOut to bcOut & {"section" & tab & "near-focus", my walk(bcNear, 0, 8)}
    end if
    set bcTops to {}
    try
        tell application "System Events" to set bcTops to UI elements of process "Messages"
    end try
    set bcN to 0
    repeat with bcTop in bcTops
        set bcN to bcN + 1
        set bcRole to my roleOf(contents of bcTop)
        if bcN > 1 and bcRole is not "AXMenuBar" and bcRole is not "AXFunctionRowTopLevelElement" then
            set bcBudget to 120
            set bcOut to bcOut & {"section" & tab & "top-" & (bcN as text), my walk(contents of bcTop, 0, 7)}
        end if
    end repeat
    my pressEscape()
    delay 0.3
    my pressEscape()
    my restoreFront(bcPrevApp)
    return bcOut
end emojiPickerProbe

-- The focused element and its ancestors, nearest first: role|id|children each (at most 12).
on focusChain()
    set bcEl to missing value
    try
        tell application "System Events" to set bcEl to value of attribute "AXFocusedUIElement" of process "Messages"
    end try
    set bcOut to {}
    repeat 12 times
        if bcEl is missing value then exit repeat
        set bcKids to 0
        try
            tell application "System Events" to set bcKids to count of UI elements of bcEl
        end try
        set bcOut to bcOut & {my roleOf(bcEl) & "|" & my attrText(bcEl, "AXIdentifier") & "|" & (bcKids as text)}
        set bcParent to missing value
        try
            tell application "System Events" to set bcParent to value of attribute "AXParent" of bcEl
        end try
        set bcEl to bcParent
    end repeat
    return my joinWith(bcOut, ";")
end focusChain

on focusAncestor(bcLevels)
    set bcEl to missing value
    try
        tell application "System Events" to set bcEl to value of attribute "AXFocusedUIElement" of process "Messages"
    end try
    repeat bcLevels times
        if bcEl is missing value then exit repeat
        set bcParent to missing value
        try
            tell application "System Events" to set bcParent to value of attribute "AXParent" of bcEl
        end try
        if bcParent is missing value then exit repeat
        set bcEl to bcParent
    end repeat
    return bcEl
end focusAncestor

on pickerProcesses()
    set bcNames to {}
    try
        tell application "System Events" to set bcNames to name of every application process whose name contains "Character" or name contains "Emoji" or name contains "Palette"
    end try
    return my joinWith(my cleanNames(bcNames), ",")
end pickerProcesses

on focusInfo()
    set bcF to missing value
    try
        tell application "System Events" to set bcF to value of attribute "AXFocusedUIElement" of process "Messages"
    end try
    if bcF is missing value then return "none"
    set bcRole to my roleOf(bcF)
    set bcId to my attrText(bcF, "AXIdentifier")
    set bcDescLen to length of (my attrText(bcF, "AXDescription"))
    set bcValLen to length of (my attrText(bcF, "AXValue"))
    set bcHint to my attrText(bcF, "AXPlaceholderValue")
    return bcRole & tab & bcId & tab & (bcDescLen as text) & tab & (bcValLen as text) & tab & bcHint
end focusInfo

on tryReply(bcWin, bcFront)
    set bcOut to {}
    set bcBalloon to my newestBalloon(bcWin)
    if bcBalloon is missing value then return {"try" & tab & "no-message"}
    set bcPrevApp to my frontBundle()
    set bcOut to bcOut & {"focus" & tab & "before" & tab & my focusInfo()}
    set bcBudget to 30
    set bcOut to bcOut & {"section" & tab & "entry-before", my walk(my spotEntry(bcWin), 0, 7)}
    if bcFront then my bringForward()
    set bcDid to my customAction(bcBalloon, {"Reply"})
    delay 0.9
    set bcOut to bcOut & {"try" & tab & (bcDid as text)}
    set bcOut to bcOut & {"focus" & tab & "after" & tab & my focusInfo()}
    set bcOut to bcOut & {"top_after" & tab & my joinWith(my topRoles(), ",")}
    set bcBudget to 30
    set bcOut to bcOut & {"section" & tab & "entry-after", my walk(my spotEntry(bcWin), 0, 7)}
    my pressEscape()
    my restoreFront(bcPrevApp)
    return bcOut
end tryReply
"""

for _name, _path in SPOTS.items():
    SIDEBAR = SIDEBAR.replace(f"__SPOT_{_name}__", spot_ref(_path))
assert "__SPOT_" not in SIDEBAR

INSPECT_MAIN, INSPECT_HANDLERS = INSPECT_RUN.split("end run\n", 1)
INSPECT_MAIN += "end run\n"

MARK_READ_SCRIPT = lines(RUN + ARGS + ELEMENTS + SIDEBAR + MARK_READ_ACT)
MARK_UNREAD_SCRIPT = lines(RUN + ARGS + ELEMENTS + SIDEBAR + MARK_UNREAD_ACT)
TAPBACK_SCRIPT = lines(RUN + ARGS + ELEMENTS + SIDEBAR + BUBBLES + EMOJI_PICKER + TAPBACK_ACT)
REPLY_SCRIPT = lines(USE_APPKIT + RUN + ARGS + ELEMENTS + SIDEBAR + BUBBLES + TEXT_FIELDS + CLIPBOARD + MENUS
                      + REPLY_TYPING + REPLY_ACT)
REPLY_AIM_SCRIPT = lines(RUN + ARGS + ELEMENTS + SIDEBAR + BUBBLES + TEXT_FIELDS + MENUS + REPLY_AIM_ACT)
REPLY_FINISH_SCRIPT = lines(USE_APPKIT + RUN + ARGS + ELEMENTS + SIDEBAR + TEXT_FIELDS + CLIPBOARD + MENUS + REPLY_TYPING
                            + REPLY_FINISH_ACT)
COMPOSE_SCRIPT = lines(USE_APPKIT + RUN + ARGS + ELEMENTS + SIDEBAR + TEXT_FIELDS + CLIPBOARD + COMPOSE_ACT)
INSPECT_SCRIPT = lines(INSPECT_MAIN + ARGS + ELEMENTS + SIDEBAR + BUBBLES + TEXT_FIELDS + MENUS + INSPECT_HANDLERS)
REPLY_TEST_SCRIPT = lines(USE_APPKIT + REPLY_TEST_MAIN + ARGS + ELEMENTS + SIDEBAR + BUBBLES + TEXT_FIELDS + CLIPBOARD
                          + MENUS + INSPECT_HANDLERS)
