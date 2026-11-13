from playwright.sync_api import sync_playwright
import time
import os
import sqlite3

DB_PATH = os.path.expanduser('~/.linkedin_agent.db')
CV_LINK = "https://drive.google.com/file/d/1i-4MK7lm3FOOD_6e6gKFuYx9l_ikHK4q/view?usp=sharing"

def send_test_poc():
    with sync_playwright() as p:
        b = p.chromium.connect_over_cdp("http://127.0.0.1:9230/default/test-poc-req")
        page = b.contexts[0].new_page()
        
        username = "siya-rani-865140384"
        first_name = "Siya"
        full_name = "Siya Rani"
        headline = "Technical Recruiter / Talent Acquisition at Exlneo"
        
        invite_url = f"https://www.linkedin.com/preload/custom-invite/?vanityName={username}"
        print(f"Navigating directly to invite route: {invite_url}...")
        page.goto(invite_url, wait_until="commit", timeout=25000)
        page.wait_for_timeout(2500)
        
        # Click Add a note with no_wait_after=True
        add_note_btn = page.locator('button:has-text("Add a note")').first
        print("Add note button visible:", add_note_btn.is_visible())
        if add_note_btn.is_visible():
            add_note_btn.click(no_wait_after=True)
            page.wait_for_timeout(1000)
            
            note_box = page.locator('textarea[name="message"], textarea#custom-message, textarea').first
            if note_box.is_visible():
                note_text = (
                    f"Hi {first_name}, saw your work in tech hiring. "
                    f"I bring 1 yr exp in product & analytics (immediate 15-day joiner) and would love to connect! "
                    f"CV: {CV_LINK}"
                )[:300]
                note_box.fill(note_text)
                page.wait_for_timeout(500)
                print(f"Note added: {note_text}")
                
        # Click Send invitation with no_wait_after=True
        send_btn = page.locator('button:has-text("Send"), button[aria-label*="Send invitation"]').first
        print("Send button visible:", send_btn.is_visible())
        if send_btn.is_visible() and send_btn.is_enabled():
            send_btn.click(no_wait_after=True)
            page.wait_for_timeout(2000)
            print(f"🎯 SUCCESS! Sent connection request with note to {full_name} ({headline})!")
            
            conn = sqlite3.connect(DB_PATH)
            cur = conn.cursor()
            cur.execute('''
                INSERT OR REPLACE INTO poc_connection_requests (id, name, headline, status, requested_at)
                VALUES (?, ?, ?, 'requested', CURRENT_TIMESTAMP)
            ''', (username, full_name, headline))
            conn.commit()
            conn.close()
            print("Logged to poc_connection_requests database table.")
            
        page.close()

if __name__ == "__main__":
    send_test_poc()
