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
    set prevApp to my frontBundle()
    try
        set answer to my act(argv)
    on error errMsg number errNum
        set answer to "error-late" & tab & (errNum as text)
    end try
    my restoreFront(prevApp)
    return answer
end run
"""

ARGS = """
on tagged(argv, tagName)
    set out to {}
    set prefix to tagName & ":"
    repeat with a in argv
        set s to a as text
        if (length of s) > (length of prefix) and s starts with prefix then
            set end of out to text ((length of prefix) + 1) thru -1 of s
        end if
    end repeat
    return out
end tagged

on taggedNumber(argv, tagName)
    set vals to my tagged(argv, tagName)
    if vals is {} then return 0
    try
        return (item 1 of vals) as integer
    on error
        return 0
    end try
end taggedNumber
"""

ELEMENTS = """
on roleOf(el)
    tell application "System Events"
        try
            return (role of el) as text
        on error
            return ""
        end try
    end tell
end roleOf

on labelOf(el)
    set lbl to ""
    tell application "System Events"
        try
            set lbl to lbl & ((description of el) as text) & linefeed
        end try
        try
            set lbl to lbl & ((name of el) as text) & linefeed
        end try
        try
            set lbl to lbl & ((value of el) as text) & linefeed
        end try
    end tell
    return lbl
end labelOf

on frameOf(el)
    tell application "System Events"
        try
            set p to position of el
            set s to size of el
            return {item 1 of p, item 2 of p, item 1 of s, item 2 of s}
        on error
            return missing value
        end try
    end tell
end frameOf

on rightEdge(el)
    set fr to my frameOf(el)
    if fr is missing value then return 0
    return (item 1 of fr) + (item 3 of fr)
end rightEdge

on frameInside(a, b)
    set x1 to item 1 of a
    set y1 to item 2 of a
    set x2 to item 1 of b
    set y2 to item 2 of b
    return x1 >= x2 - 2 and y1 >= y2 - 2 and x1 + (item 3 of a) <= x2 + (item 3 of b) + 2 and y1 + (item 4 of a) <= y2 + (item 4 of b) + 2
end frameInside

on sameBubble(a, b)
    if a is missing value or b is missing value then return false
    return my frameInside(a, b) or my frameInside(b, a)
end sameBubble

on lineMatches(lbl, s)
    repeat with aLine in paragraphs of lbl
        set t to aLine as text
        if t is s or t starts with (s & ",") or t starts with (s & " ") then return true
    end repeat
    return false
end lineMatches

on frontBundle()
    tell application "System Events"
        try
            return (bundle identifier of (first application process whose frontmost is true)) as text
        on error
            return ""
        end try
    end tell
end frontBundle

on restoreFront(prevApp)
    if prevApp is "" or prevApp is "com.apple.MobileSMS" then return
    tell application "System Events"
        try
            if frontmost of process "Messages" then
                set frontmost of (first application process whose bundle identifier is prevApp) to true
            end if
        end try
    end tell
end restoreFront

on bringForward()
    tell application "System Events"
        try
            set frontmost of process "Messages" to true
        end try
    end tell
    delay 0.3
end bringForward

on pressEscape()
    my bringForward()
    tell application "System Events"
        try
            key code 53
        end try
    end tell
    delay 0.2
end pressEscape
"""

# Find the conversation's row in the sidebar, in one pass over the window: a label line that is
# exactly one of the names (or "Name, …") wins, the earliest name first; the L: names may also
# match a label's beginning. Matches are climbed to their row; rows win over other elements.
SIDEBAR = """
on bestName(lbl, names, strict)
    repeat with i from 1 to (count of names)
        set needle to (item i of names) as text
        if (length of needle) > 0 then
            repeat with aLine in paragraphs of lbl
                set t to aLine as text
                if strict then
                    if t is needle or t starts with (needle & ",") then return i
                else
                    if t starts with needle then return i
                end if
            end repeat
        end if
    end repeat
    return 0
end bestName

on findConversationRow(win, strictNames, looseNames)
    set allNames to looseNames & strictNames
    set looseCount to count of looseNames
    set bestStrict to 0
    set strictRow to missing value
    set bestLoose to 0
    set looseRow to missing value
    set fallbackEl to missing value
    set seen to 0
    tell application "System Events" to set allEls to entire contents of win
    repeat with el in allEls
        set seen to seen + 1
        if seen > 2500 then exit repeat
        if my roleOf(el) is in {"AXRow", "AXCell", "AXButton", "AXGroup", "AXStaticText"} then
            set lbl to my labelOf(el)
            set k to my bestName(lbl, allNames, true)
            if k > 0 and (bestStrict = 0 or k < bestStrict) then
                set rowEl to my climbToRow(contents of el)
                if rowEl is not missing value then
                    set bestStrict to k
                    set strictRow to rowEl
                    if k = 1 then exit repeat
                else if fallbackEl is missing value then
                    set fallbackEl to contents of el
                end if
            else if k = 0 and looseCount > 0 then
                set k2 to my bestName(lbl, items 1 thru looseCount of allNames, false)
                if k2 > 0 and (bestLoose = 0 or k2 < bestLoose) then
                    set rowEl to my climbToRow(contents of el)
                    if rowEl is not missing value then
                        set bestLoose to k2
                        set looseRow to rowEl
                    end if
                end if
            end if
        end if
    end repeat
    if strictRow is not missing value then return strictRow
    if looseRow is not missing value then return looseRow
    return fallbackEl
end findConversationRow

on climbToRow(el)
    tell application "System Events"
        try
            if role of el is in {"AXRow", "AXCell"} then return el
        end try
        set p to el
        repeat 6 times
            try
                set p to value of attribute "AXParent" of p
                if role of p is in {"AXRow", "AXCell"} then return p
            on error
                exit repeat
            end try
        end repeat
    end tell
    return missing value
end climbToRow

on selectRow(rowEl)
    tell application "System Events"
        try
            if role of rowEl is not in {"AXRow", "AXCell"} then return false
        on error
            return false
        end try
        try
            set value of attribute "AXSelected" of rowEl to true
            delay 0.3
            if (value of attribute "AXSelected" of rowEl) is true then return true
        end try
        try
            perform action "AXPress" of rowEl
            delay 0.3
            if (value of attribute "AXSelected" of rowEl) is true then return true
        end try
    end tell
    return false
end selectRow
"""

# Find a message bubble in the transcript (right of the sidebar), newest first: walk the window
# backwards; nested elements of one bubble (same or enclosing frame) count once.
BUBBLES = """
on matchesBubble(lbl, needles, exacts)
    repeat with t in needles
        if lbl contains (t as text) then return true
    end repeat
    if (count of exacts) > 0 then
        repeat with aLine in paragraphs of lbl
            set s to aLine as text
            repeat with x in exacts
                set w to x as text
                if s is w or s starts with (w & ",") or s starts with (w & " ") or s contains (", " & w & ",") then return true
            end repeat
        end repeat
    end if
    return false
end matchesBubble

on mentionsAny(lbl, senders)
    repeat with s in senders
        if lbl contains (s as text) then return true
    end repeat
    return false
end mentionsAny

on findBubble(win, minX, needles, exacts, senders, skipSender, skipAny)
    tell application "System Events" to set allEls to entire contents of win
    set senderHits to 0
    set anyHits to 0
    set anyPick to missing value
    set pickedAt to 0
    set lastFrame to missing value
    set lastSenderFrame to missing value
    set examined to 0
    repeat with i from (count of allEls) to 1 by -1
        set examined to examined + 1
        if examined > 3000 then exit repeat
        if anyPick is not missing value and examined - pickedAt > 400 then exit repeat
        set el to item i of allEls
        if my roleOf(el) is in {"AXGroup", "AXStaticText", "AXTextArea", "AXTextField", "AXCell", "AXImage", "AXButton"} then
            set lbl to my labelOf(el)
            if my matchesBubble(lbl, needles, exacts) then
                set fr to my frameOf(el)
                if fr is not missing value then
                    if (item 1 of fr) >= minX then
                        if not my sameBubble(fr, lastFrame) then
                            set lastFrame to fr
                            set anyHits to anyHits + 1
                            if anyHits = skipAny + 1 then
                                set anyPick to el
                                set pickedAt to examined
                                if (count of senders) = 0 then return el
                            end if
                        end if
                        if (count of senders) > 0 then
                            if my mentionsAny(lbl, senders) then
                                if not my sameBubble(fr, lastSenderFrame) then
                                    set lastSenderFrame to fr
                                    set senderHits to senderHits + 1
                                    if senderHits = skipSender + 1 then return el
                                end if
                            end if
                        end if
                    end if
                end if
            end if
        end if
    end repeat
    return anyPick
end findBubble
"""

MENUS = """
on openMenu(el, win)
    set menuHost to el
    set shown to false
    tell application "System Events"
        repeat 4 times
            try
                perform action "AXShowMenu" of menuHost
                set shown to true
                exit repeat
            on error
                try
                    set menuHost to value of attribute "AXParent" of menuHost
                on error
                    exit repeat
                end try
            end try
        end repeat
        if not shown then return missing value
        delay 0.4
        try
            return menu 1 of menuHost
        end try
        try
            return menu 1 of win
        end try
    end tell
    return missing value
end openMenu

on closeMenu(theMenu)
    tell application "System Events"
        try
            perform action "AXCancel" of theMenu
            return
        end try
    end tell
    my pressEscape()
end closeMenu

on menuItemStarting(theMenu, prefixes)
    tell application "System Events"
        try
            set itemsList to menu items of theMenu
        on error
            return missing value
        end try
        repeat with mi in itemsList
            set t to ""
            try
                set t to (name of mi) as text
            end try
            repeat with p in prefixes
                if t starts with (p as text) then return (contents of mi)
            end repeat
        end repeat
    end tell
    return missing value
end menuItemStarting

on menuItemMatching(theMenu, synonyms)
    tell application "System Events"
        try
            set els to entire contents of theMenu
        on error
            return missing value
        end try
    end tell
    repeat with el in els
        if my roleOf(el) is in {"AXMenuItem", "AXButton", "AXRadioButton", "AXCheckBox"} then
            set lbl to my labelOf(el)
            repeat with s in synonyms
                if my lineMatches(lbl, s as text) then return (contents of el)
            end repeat
        end if
    end repeat
    return missing value
end menuItemMatching
"""

TAPBACK_BUTTONS = """
on findReactionButton(synonyms)
    tell application "System Events"
        tell process "Messages"
            set wins to windows
        end tell
    end tell
    repeat with w in wins
        tell application "System Events" to set els to entire contents of w
        set examined to 0
        repeat with i from (count of els) to 1 by -1
            set examined to examined + 1
            if examined > 1500 then exit repeat
            set el to item i of els
            if my roleOf(el) is in {"AXButton", "AXRadioButton", "AXCheckBox"} then
                set lbl to my labelOf(el)
                repeat with s in synonyms
                    if my lineMatches(lbl, s as text) then return el
                end repeat
            end if
        end repeat
    end repeat
    return missing value
end findReactionButton
"""

TEXT_FIELDS = """
on focusedTextField()
    tell application "System Events"
        tell process "Messages"
            try
                set f to value of attribute "AXFocusedUIElement"
                if role of f is in {"AXTextArea", "AXTextField"} then return f
            end try
        end tell
    end tell
    return missing value
end focusedTextField

on fieldText(f)
    tell application "System Events"
        try
            set v to value of f
            if v is missing value then return ""
            return v as text
        on error
            return "?"
        end try
    end tell
end fieldText

on clipboardIsText()
    try
        set clipInfo to (clipboard info)
    on error
        return false
    end try
    repeat with pair in clipInfo
        set c to item 1 of pair
        if c is not in {string, Unicode text, text, «class utf8», «class ut16»} then return false
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

on setClip(t)
    try
        set the clipboard to t
    end try
end setClip
"""

FIND_CONVERSATION = """
    set looseNames to my tagged(argv, "L")
    set strictNames to my tagged(argv, "N")
"""

# --------------------------------------------------------------------------- mark read

MARK_READ_ACT = """
on act(argv)
    set looseNames to my tagged(argv, "L")
    set strictNames to my tagged(argv, "N")
    tell application "System Events"
        if not (exists process "Messages") then return "not-running"
        tell process "Messages"
            if (count of windows) is 0 then return "no-window"
            set win to window 1
        end tell
    end tell
    set theRow to my findConversationRow(win, strictNames, looseNames)
    if theRow is missing value then return "row-not-found"
    if my selectRow(theRow) then
        tell application "System Events"
            tell process "Messages"
                try
                    set markItem to menu item "Mark as Read" of menu 1 of menu bar item "Conversation" of menu bar 1
                    if enabled of markItem then
                        click markItem
                        return "ok-menu"
                    end if
                end try
            end tell
        end tell
    end if
    set theMenu to my openMenu(theRow, win)
    if theMenu is missing value then return "no-mark-item"
    set markItem to my menuItemStarting(theMenu, {"Mark as Read"})
    if markItem is missing value then
        my closeMenu(theMenu)
        return "no-mark-item"
    end if
    tell application "System Events" to click markItem
    return "ok-context"
end act
"""

# --------------------------------------------------------------------------- tapback

TAPBACK_ACT = """
on act(argv)
    set looseNames to my tagged(argv, "L")
    set strictNames to my tagged(argv, "N")
    set needles to my tagged(argv, "T")
    set exacts to my tagged(argv, "W")
    set senders to my tagged(argv, "S")
    set skipSender to my taggedNumber(argv, "K")
    set skipAny to my taggedNumber(argv, "J")
    set synonyms to my tagged(argv, "R")
    if synonyms is {} then return "no-kind"
    try
        tell application "System Events"
            if not (exists process "Messages") then return "not-running"
            tell process "Messages"
                if (count of windows) is 0 then return "no-window"
                set win to window 1
            end tell
        end tell
        set theRow to my findConversationRow(win, strictNames, looseNames)
        if theRow is missing value then return "row-not-found"
        if not my selectRow(theRow) then return "not-selected"
        delay 0.8
        set bubble to my findBubble(win, my rightEdge(theRow), needles, exacts, senders, skipSender, skipAny)
        if bubble is missing value then return "bubble-not-found"
        set theMenu to my openMenu(bubble, win)
        if theMenu is missing value then return "no-menu"
        set tapItem to my menuItemStarting(theMenu, {"Tapback", "React"})
    on error errMsg number errNum
        return "error" & tab & (errNum as text)
    end try
    if tapItem is missing value then
        set reactItem to my menuItemMatching(theMenu, synonyms)
        if reactItem is missing value then
            my closeMenu(theMenu)
            return "no-tapback-item"
        end if
        tell application "System Events" to click reactItem
        return "chosen-menu"
    end if
    tell application "System Events" to click tapItem
    delay 0.6
    set btn to my findReactionButton(synonyms)
    if btn is missing value then
        my pressEscape()
        return "no-tapback-button"
    end if
    tell application "System Events" to perform action "AXPress" of btn
    return "chosen-overlay"
end act
"""

# --------------------------------------------------------------------------- reply
# Stage 1 can't change anything in Messages (a failure there means "send it the normal way").
# From stage 2 on the text may be in Messages, so failures are reported, never retried. Return is
# only pressed when the focused field holds exactly the reply.

REPLY_ACT = """
on act(argv)
    set looseNames to my tagged(argv, "L")
    set strictNames to my tagged(argv, "N")
    set needles to my tagged(argv, "T")
    set exacts to my tagged(argv, "W")
    set senders to my tagged(argv, "S")
    set skipSender to my taggedNumber(argv, "K")
    set skipAny to my taggedNumber(argv, "J")
    set texts to my tagged(argv, "M")
    if texts is {} then return "no-text"
    set replyText to item 1 of texts
    try
        tell application "System Events"
            if not (exists process "Messages") then return "not-running"
            tell process "Messages"
                if (count of windows) is 0 then return "no-window"
                set win to window 1
            end tell
        end tell
        set theRow to my findConversationRow(win, strictNames, looseNames)
        if theRow is missing value then return "row-not-found"
        if not my selectRow(theRow) then return "not-selected"
        delay 0.8
        set bubble to my findBubble(win, my rightEdge(theRow), needles, exacts, senders, skipSender, skipAny)
        if bubble is missing value then return "bubble-not-found"
        set theMenu to my openMenu(bubble, win)
        if theMenu is missing value then return "no-menu"
        set replyItem to my menuItemStarting(theMenu, {"Reply"})
        if replyItem is missing value then
            my closeMenu(theMenu)
            return "no-reply-item"
        end if
        tell application "System Events" to click replyItem
        delay 0.7
        set replyBox to my focusedTextField()
        if replyBox is missing value then return "no-reply-field"
        if (my fieldText(replyBox)) is not "" then return "field-busy"
    on error errMsg number errNum
        return "error" & tab & (errNum as text)
    end try
    set how to ""
    try
        tell application "System Events" to set value of replyBox to replyText
        delay 0.2
    end try
    considering case
        if (my fieldText(replyBox)) is replyText then set how to "axvalue"
    end considering
    if how is "" then
        if (my fieldText(replyBox)) is not "" then return "typed-unknown"
        if not my clipboardIsText() then return "clipboard-busy"
        my bringForward()
        tell application "System Events"
            try
                set focused of replyBox to true
            end try
        end tell
        delay 0.15
        set f to my focusedTextField()
        if f is missing value then return "nothing-typed"
        if (my fieldText(f)) is not "" then return "nothing-typed"
        set saved to my clipText()
        my setClip(replyText)
        tell application "System Events" to key code 9 using command down
        delay 0.4
        my setClip(saved)
        set f to my focusedTextField()
        if f is missing value then return "typed-unknown"
        considering case
            if (my fieldText(f)) is replyText then set how to "paste"
        end considering
        if how is "" then return "typed-unknown"
    end if
    my bringForward()
    tell application "System Events"
        try
            set focused of replyBox to true
        end try
    end tell
    delay 0.1
    set f to my focusedTextField()
    if f is missing value then return "typed-not-sent"
    set ready to false
    considering case
        if (my fieldText(f)) is replyText then set ready to true
    end considering
    if not ready then return "typed-not-sent"
    tell application "System Events" to key code 36
    delay 0.6
    if (my fieldText(f)) is "" then return "sent-" & how
    return "returned-unconfirmed"
end act
"""

# --------------------------------------------------------------------------- permission probe
# Asks for "control System Events" (Automation) only; never touches Messages.

PROBE_SCRIPT = lines("""
on run argv
    tell application "System Events"
        set frontName to name of first application process whose frontmost is true
    end tell
    return "ready"
end run
""")

# --------------------------------------------------------------------------- doctor --messages-ui
# Prints roles, counts, booleans/lengths and menu/button titles only: never names, descriptions or
# values of conversations or messages. The bubble part opens the newest bubble's context menu,
# the Tapback picker (without choosing anything) and the Reply field (without typing), each
# closed again with Escape.

INSPECT_RUN = """
property budget : 400

on run argv
    set outLines to {}
    tell application "System Events"
        try
            set probe to count of UI elements of process "Finder"
            set end of outLines to "accessibility" & tab & "allowed"
        on error errMsg number errNum
            set end of outLines to "accessibility" & tab & "denied" & tab & (errNum as text)
            return my joinWith(outLines, linefeed)
        end try
        if not (exists process "Messages") then
            set end of outLines to "messages_running" & tab & "no"
            return my joinWith(outLines, linefeed)
        end if
        set end of outLines to "messages_running" & tab & "yes"
        tell process "Messages"
            set end of outLines to "windows" & tab & ((count of windows) as text)
            try
                set end of outLines to "menu_bar" & tab & my joinWith(my cleanNames(name of every menu bar item of menu bar 1), " | ")
            end try
            try
                set end of outLines to "conversation_menu" & tab & my joinWith(my cleanNames(name of every menu item of menu 1 of menu bar item "Conversation" of menu bar 1), " | ")
            end try
            set winCount to count of windows
            if winCount > 0 then set win to window 1
        end tell
    end tell
    if winCount > 0 then
        set end of outLines to my walk(win, 0, 8)
        set end of outLines to my rowStats(win)
        set end of outLines to my inspectBubble(win)
    end if
    return my joinWith(outLines, linefeed)
end run

on rowStats(win)
    set rowCount to 0
    set withDesc to 0
    set withName to 0
    set withValue to 0
    set maxDesc to 0
    set maxName to 0
    set maxValue to 0
    tell application "System Events" to set allEls to entire contents of win
    repeat with el in allEls
        if my roleOf(el) is in {"AXRow", "AXCell"} then
            set rowCount to rowCount + 1
            set n to my textLength(el, "description")
            if n > 0 then set withDesc to withDesc + 1
            if n > maxDesc then set maxDesc to n
            set n to my textLength(el, "name")
            if n > 0 then set withName to withName + 1
            if n > maxName then set maxName to n
            set n to my textLength(el, "value")
            if n > 0 then set withValue to withValue + 1
            if n > maxValue then set maxValue to n
        end if
    end repeat
    return "rows" & tab & rowCount & tab & withDesc & tab & withName & tab & withValue & tab & maxDesc & tab & maxName & tab & maxValue
end rowStats

on selectedRowEdge(win)
    tell application "System Events" to set allEls to entire contents of win
    set seen to 0
    repeat with el in allEls
        set seen to seen + 1
        if seen > 2500 then exit repeat
        if my roleOf(el) is in {"AXRow", "AXCell"} then
            tell application "System Events"
                try
                    if (value of attribute "AXSelected" of el) is true then return my rightEdge(contents of el)
                end try
            end tell
        end if
    end repeat
    set fr to my frameOf(win)
    if fr is missing value then return 0
    return (item 1 of fr) + ((item 3 of fr) * 0.3)
end selectedRowEdge

on newestBubble(win, minX)
    tell application "System Events" to set allEls to entire contents of win
    set examined to 0
    repeat with i from (count of allEls) to 1 by -1
        set examined to examined + 1
        if examined > 1500 then exit repeat
        set el to item i of allEls
        if my roleOf(el) is in {"AXGroup", "AXStaticText", "AXCell", "AXImage"} then
            set fr to my frameOf(el)
            if fr is not missing value then
                if (item 1 of fr) >= minX then
                    set acts to {}
                    tell application "System Events"
                        try
                            set acts to name of every action of el
                        end try
                    end tell
                    if acts contains "AXShowMenu" then return el
                end if
            end if
        end if
    end repeat
    return missing value
end newestBubble

on buttonLabels()
    set out to {}
    tell application "System Events"
        tell process "Messages"
            set wins to windows
        end tell
    end tell
    repeat with w in wins
        tell application "System Events" to set els to entire contents of w
        set seen to 0
        repeat with el in els
            set seen to seen + 1
            if seen > 3000 then exit repeat
            if my roleOf(el) is in {"AXButton", "AXRadioButton", "AXCheckBox"} then
                tell application "System Events"
                    try
                        set d to (description of el) as text
                        if d is not "" and d is not "missing value" then set end of out to d
                    end try
                end tell
            end if
        end repeat
    end repeat
    return out
end buttonLabels

on inspectBubble(win)
    set outLines to {}
    set prevApp to my frontBundle()
    set bubble to my newestBubble(win, my selectedRowEdge(win))
    if bubble is missing value then return "bubble" & tab & "none"
    set acts to {}
    tell application "System Events"
        try
            set acts to name of every action of bubble
        end try
    end tell
    set end of outLines to "bubble" & tab & my roleOf(bubble) & tab & my joinWith(my cleanNames(acts), ",")
    set theMenu to my openMenu(bubble, win)
    if theMenu is missing value then
        set end of outLines to "bubble_menu" & tab & "(none)"
        return my joinWith(outLines, linefeed)
    end if
    set titles to {}
    tell application "System Events"
        try
            set titles to name of every menu item of theMenu
        end try
    end tell
    set end of outLines to "bubble_menu" & tab & my joinWith(my cleanNames(titles), " | ")
    set tapItem to my menuItemStarting(theMenu, {"Tapback", "React"})
    if tapItem is missing value then
        set end of outLines to "tapback_item" & tab & "no"
        my closeMenu(theMenu)
    else
        set end of outLines to "tapback_item" & tab & "yes"
        set beforeLabels to my buttonLabels()
        tell application "System Events" to click tapItem
        delay 0.6
        set fresh to {}
        repeat with x in my buttonLabels()
            if (contents of x) is not in beforeLabels then set end of fresh to (contents of x)
        end repeat
        set end of outLines to "tapback_buttons" & tab & my joinWith(fresh, " | ")
        my pressEscape()
    end if
    set theMenu to my openMenu(bubble, win)
    if theMenu is not missing value then
        set replyItem to my menuItemStarting(theMenu, {"Reply"})
        if replyItem is missing value then
            set end of outLines to "reply_item" & tab & "no"
            my closeMenu(theMenu)
        else
            set end of outLines to "reply_item" & tab & "yes"
            tell application "System Events" to click replyItem
            delay 0.7
            set f to my focusedTextField()
            if f is missing value then
                set end of outLines to "reply_field" & tab & "none"
            else
                set settableValue to false
                tell application "System Events"
                    try
                        set settableValue to settable of attribute "AXValue" of f
                    end try
                end tell
                set end of outLines to "reply_field" & tab & my roleOf(f) & tab & (settableValue as text)
            end if
            my pressEscape()
        end if
    end if
    my restoreFront(prevApp)
    return my joinWith(outLines, linefeed)
end inspectBubble

on walk(el, depth, maxDepth)
    set outLines to {}
    if budget is less than or equal to 0 then return ""
    set budget to budget - 1
    tell application "System Events"
        set r to "?"
        try
            set r to role of el
        end try
        set kids to {}
        try
            set kids to UI elements of el
        end try
        set end of outLines to "tree" & tab & (depth as text) & tab & r & tab & ((count of kids) as text)
        if depth < maxDepth then
            set i to 0
            repeat with k in kids
                set i to i + 1
                if i > 12 or budget is less than or equal to 0 then exit repeat
                set end of outLines to my walk(k, depth + 1, maxDepth)
            end repeat
        end if
    end tell
    return my joinWith(outLines, linefeed)
end walk

on textLength(el, which)
    tell application "System Events"
        try
            if which is "description" then set t to description of el
            if which is "name" then set t to name of el
            if which is "value" then set t to value of el
            return length of (t as text)
        on error
            return 0
        end try
    end tell
end textLength

on cleanNames(lst)
    set out to {}
    repeat with x in lst
        try
            set end of out to (x as text)
        on error
            set end of out to "-"
        end try
    end repeat
    return out
end cleanNames

on joinWith(lst, sep)
    set saved to AppleScript's text item delimiters
    set AppleScript's text item delimiters to sep
    set s to lst as text
    set AppleScript's text item delimiters to saved
    return s
end joinWith
"""

MARK_READ_SCRIPT = lines(RUN + ARGS + ELEMENTS + SIDEBAR + MENUS + MARK_READ_ACT)
TAPBACK_SCRIPT = lines(RUN + ARGS + ELEMENTS + SIDEBAR + BUBBLES + MENUS + TAPBACK_BUTTONS + TAPBACK_ACT)
REPLY_SCRIPT = lines(RUN + ARGS + ELEMENTS + SIDEBAR + BUBBLES + MENUS + TEXT_FIELDS + REPLY_ACT)
INSPECT_SCRIPT = lines(INSPECT_RUN + ELEMENTS + MENUS + TEXT_FIELDS)
