#!/bin/bash
# KRX Safari 세션 keep-alive (25분마다 모든 탭에서 "연장" 버튼 클릭)
while true; do
    RESULT=$(osascript -e 'tell application "Safari"
        set foundKrx to false
        set extended to false
        repeat with d in documents
            try
                set pageURL to URL of d
                if pageURL contains "krx.co.kr" then
                    set foundKrx to true
                    set jsResult to do JavaScript "
                        var btns = document.querySelectorAll(\"button,a\");
                        var clicked = false;
                        btns.forEach(function(b) {
                            if (b.textContent.trim() === \"연장\" && !clicked) {
                                b.click();
                                clicked = true;
                            }
                        });
                        clicked ? \"EXTENDED\" : \"NO_BTN\";
                    " in d
                    if jsResult is "EXTENDED" then
                        set extended to true
                    end if
                end if
            on error
            end try
        end repeat
        if extended then
            return "EXTENDED"
        else if foundKrx then
            return "NO_BTN"
        else
            return "NOT_KRX"
        end if
    end tell' 2>/dev/null)
    echo "[$(date '+%H:%M')] $RESULT"
    sleep 1500  # 25분
done
