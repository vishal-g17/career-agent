import os
import sys
import base64
import pypdf
from playwright.sync_api import sync_playwright

OUTPUT_HTML = '/tmp/latex_cv_render.html'
OUTPUT_PDF = os.path.expanduser('~/Downloads/Vishal_Gupta_Resume.pdf')
REPO_PDF = '/Users/admin/linkedin-multiagent/data/resume.pdf'

# Load KaTeX Computer Modern Fonts as base64
with open('/tmp/katex_fonts/KaTeX_Main-Regular.ttf', 'rb') as f:
    reg_b64 = base64.b64encode(f.read()).decode('utf-8')
with open('/tmp/katex_fonts/KaTeX_Main-Bold.ttf', 'rb') as f:
    bold_b64 = base64.b64encode(f.read()).decode('utf-8')
with open('/tmp/katex_fonts/KaTeX_Main-Italic.ttf', 'rb') as f:
    ital_b64 = base64.b64encode(f.read()).decode('utf-8')

HTML_TEMPLATE = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>Vishal Gupta - Resume</title>
<style>
  @font-face {{
    font-family: 'Computer Modern';
    src: url('data:font/truetype;charset=utf-8;base64,{reg_b64}') format('truetype');
    font-weight: normal;
    font-style: normal;
  }}
  @font-face {{
    font-family: 'Computer Modern';
    src: url('data:font/truetype;charset=utf-8;base64,{bold_b64}') format('truetype');
    font-weight: bold;
    font-style: normal;
  }}
  @font-face {{
    font-family: 'Computer Modern';
    src: url('data:font/truetype;charset=utf-8;base64,{ital_b64}') format('truetype');
    font-weight: normal;
    font-style: italic;
  }}

  @page {{
    size: letter;
    margin: 0;
  }}

  * {{
    box-sizing: border-box;
    margin: 0;
    padding: 0;
  }}

  body {{
    font-family: 'Computer Modern', 'Latin Modern Roman', serif;
    color: #000000;
    background-color: #ffffff;
    font-size: 10.0pt;
    line-height: 1.28;
    padding: 0.42in 0.48in;
    width: 8.5in;
    margin: 0 auto;
    -webkit-font-smoothing: antialiased;
  }}

  /* HEADER */
  .header {{
    text-align: center;
    margin-bottom: 0.10in;
  }}
  .name {{
    font-size: 23pt;
    font-weight: bold;
    letter-spacing: 0.5px;
    color: #000000;
    text-transform: uppercase;
    margin-bottom: 4px;
  }}
  .contact-bar {{
    font-size: 9.6pt;
    color: #111111;
    display: flex;
    justify-content: center;
    align-items: center;
    gap: 8px;
  }}
  .contact-bar a {{
    color: #000000;
    text-decoration: none;
  }}
  .contact-bar a:hover {{
    text-decoration: underline;
  }}
  .sep {{
    color: #666666;
    margin: 0 1px;
  }}

  /* SECTIONS */
  .section {{
    margin-bottom: 0.11in;
  }}
  .section:last-child {{
    margin-bottom: 0;
  }}
  .section-title {{
    font-size: 11.2pt;
    font-weight: bold;
    text-transform: uppercase;
    letter-spacing: 0.6px;
    color: #000000;
    border-bottom: 1px solid #000000;
    padding-bottom: 1.5px;
    margin-bottom: 5px;
  }}

  /* SUBHEADINGS */
  .item {{
    margin-bottom: 5.5px;
  }}
  .item:last-child {{
    margin-bottom: 0;
  }}
  .row-main {{
    display: flex;
    justify-content: space-between;
    align-items: baseline;
    font-size: 10.2pt;
  }}
  .row-main .left {{
    font-weight: bold;
    color: #000000;
  }}
  .row-main .right {{
    font-weight: normal;
    color: #000000;
  }}
  .row-sub {{
    display: flex;
    justify-content: space-between;
    align-items: baseline;
    font-size: 9.8pt;
    font-style: italic;
    color: #111111;
    margin-bottom: 2px;
  }}
  .row-sub .left {{
    font-style: italic;
  }}
  .row-sub .right {{
    font-style: italic;
  }}

  /* BULLETS */
  ul.bullets {{
    list-style-type: disc;
    margin-left: 17px;
    margin-top: 1.5px;
  }}
  ul.bullets li {{
    font-size: 9.6pt;
    line-height: 1.27;
    color: #000000;
    margin-bottom: 2px;
    padding-left: 1px;
    text-align: justify;
  }}
  ul.bullets li strong {{
    font-weight: bold;
    color: #000000;
  }}

  /* SKILLS */
  .skills-box {{
    font-size: 9.7pt;
    line-height: 1.34;
    color: #000000;
  }}
  .skills-box p {{
    margin-bottom: 2px;
  }}
  .skills-box strong {{
    font-weight: bold;
  }}
</style>
</head>
<body>

  <!-- HEADER -->
  <div class="header">
    <div class="name">Vishal Gupta</div>
    <div class="contact-bar">
      <span>+91 8368387245</span>
      <span class="sep">|</span>
      <span><a href="mailto:vishal90301@gmail.com">vishal90301@gmail.com</a></span>
      <span class="sep">|</span>
      <span><a href="https://www.linkedin.com/in/vshal-g007" target="_blank">linkedin.com/in/vshal-g007</a></span>
      <span class="sep">|</span>
      <span><a href="https://github.com/vishal-g17" target="_blank">github.com/vishal-g17</a></span>
      <span class="sep">|</span>
      <span><a href="https://codolio.com/profile/GtjCJNug" target="_blank">Coding Portfolio</a></span>
    </div>
  </div>

  <!-- EDUCATION -->
  <div class="section">
    <div class="section-title">Education</div>
    <div class="item">
      <div class="row-main">
        <span class="left">Netaji Subhas University of Technology (NSUT)</span>
        <span class="right">2022 &ndash; 2026</span>
      </div>
      <div class="row-sub">
        <span class="left">B.Tech in Computer Science & Engineering (Specialization in Mathematics & Computing)</span>
        <span class="right">New Delhi, India</span>
      </div>
    </div>
  </div>

  <!-- WORK EXPERIENCE -->
  <div class="section">
    <div class="section-title">Work Experience</div>

    <!-- Magicpin -->
    <div class="item">
      <div class="row-main">
        <span class="left">Magicpin</span>
        <span class="right">Aug’26 &ndash; Present</span>
      </div>
      <div class="row-sub">
        <span class="left">Tech Product Analyst</span>
        <span class="right">Gurugram, India</span>
      </div>
      <ul class="bullets">
        <li>Built an automated merchant reactivation pipeline for inactive/high-cancellation merchants, <strong>reducing cancellations from 10–12% to 4–6%</strong>.</li>
        <li>Built automated ingestion and pricing pipeline integrating competitor crawlers and POS webhooks to eliminate catalog deficits (items &le; 8, image coverage &lt; 60%); deployed fuzzy dish-matching to enforce 1:1 base MRP parity against competitor benchmarks, eliminating friction for low-CVR merchants (&lt;1%) while protecting platform discount layers.</li>
        <li>Engineered real-time merchant funnel telemetry (DAU &rarr; Views &rarr; Carts &rarr; Orders); diagnosed low-conversion cohorts (&lt;1% CVR) and resolved listing glitches on premium surfaces (Magic9).</li>
        <li>Developing merchant-level opportunity analysis across 7 mega cities, mapping locality- and item-level demand/search gaps against inactive supply to drive targeted activation and onboarding.</li>
      </ul>
    </div>

    <!-- Lifelong Online -->
    <div class="item">
      <div class="row-main">
        <span class="left">Lifelong Online</span>
        <span class="right">Feb’26 &ndash; Aug’26</span>
      </div>
      <div class="row-sub">
        <span class="left">Business Analyst</span>
        <span class="right">Gurugram, India</span>
      </div>
      <ul class="bullets">
        <li>Built an end-to-end ERP automation pipeline (Python, Playwright, pandas) that auto-downloads, merges, and consolidates SKU-level PO/GRN/ATP data &mdash; <strong>eliminating 3&ndash;4 hours of daily manual reporting</strong>.</li>
        <li>Engineered cross-source data pipelines merging 6+ datasets (Item Master, PO files, Blinkit FC, SKU master) with audit trails flagging unmapped records for procurement accuracy.</li>
        <li>Developed and deployed an internal Sticker Generator platform from scratch, enabling business teams to self-serve custom product sticker creation.</li>
        <li>Built an automated quick-commerce competitive pricing scraper using Python and Playwright to extract real-time product prices.</li>
      </ul>
    </div>

    <!-- NOM -->
    <div class="item">
      <div class="row-main">
        <span class="left">NOM</span>
        <span class="right">May’25 &ndash; Nov’25</span>
      </div>
      <div class="row-sub">
        <span class="left">Founding Product Manager</span>
        <span class="right">Remote</span>
      </div>
      <ul class="bullets">
        <li>Conducted market research and competitor benchmarking to inform product strategy and validate core features during early-stage development.</li>
        <li>Designed product flow architecture integrating LLM capabilities for scalable feature development; owned QA testing and collaborated with developers to refine features.</li>
        <li>Architected LLM workflows, structured prompt engineering, and automated evaluation pipelines to improve output accuracy and coverage.</li>
      </ul>
    </div>
  </div>

  <!-- PROJECTS -->
  <div class="section">
    <div class="section-title">Projects</div>
    <div class="item">
      <div class="row-main">
        <span class="left">Autonomous Multi-Agent Career & Outreach Engine</span>
        <span class="right">Python, Playwright, SQLite, Discord API, Gemini LLM</span>
      </div>
      <ul class="bullets">
        <li><strong>Distributed Multi-Agent Architecture:</strong> Architected a production-grade multi-agent system orchestrating 4 specialized autonomous workers (DOM Form Filler, Lead Enrichment Engine, DNS/SMTP Verifier, and Discord Remote Controller).</li>
        <li><strong>Application Automation Pipeline:</strong> Built an autonomous job application pipeline using Playwright CDP that dynamically solves multi-step forms, handles contenteditable screening chatbots, and navigates external career redirects across Naukri, Indeed, and Google Forms &mdash; <strong>submitting 100+ applications autonomously</strong>.</li>
        <li><strong>Pipeline & Anti-Bot Engineering:</strong> Designed transactional SQLite queue pipelines processing 4,000+ candidate profiles with stealth CDP injection, humanized mouse-wheel physics, and velocity throttles to bypass anti-bot detections.</li>
        <li><strong>Remote Command Center:</strong> Integrated a conversational Discord bot daemon over WebSockets enabling real-time mobile telemetry, asynchronous shell execution, and live pipeline control.</li>
      </ul>
    </div>
  </div>

  <!-- TECHNICAL SKILLS -->
  <div class="section">
    <div class="section-title">Technical Skills</div>
    <div class="skills-box">
      <p><strong>Programming & Languages:</strong> Python, SQL, C++, C, JavaScript, Bash</p>
      <p><strong>Tools & Frameworks:</strong> Playwright, SQLite, Git, Discord API, Cloudflare Tunnels, Pandas, Linux Daemons, Postman</p>
      <p><strong>AI & Automation:</strong> Multi-Agent Systems, Agentic Workflows, Google Gemini API, Prompt Engineering, Web Automation</p>
      <p><strong>Core Competencies:</strong> Product Analytics, Funnel Telemetry, Conversion Rate Optimization (CRO), ERP Automation, System Design</p>
    </div>
  </div>

  <!-- POSITIONS OF RESPONSIBILITY -->
  <div class="section">
    <div class="section-title">Positions of Responsibility</div>
    <div class="item">
      <div class="row-main">
        <span class="left">Moksha Innovision 2024 (NSUT Annual Cultural Fest)</span>
        <span class="right">Dec’23 &ndash; Mar’24</span>
      </div>
      <div class="row-sub">
        <span class="left">Sponsorship & Collaboration Lead</span>
        <span class="right">New Delhi, India</span>
      </div>
      <ul class="bullets">
        <li>Secured monetary and in-kind corporate sponsorships; negotiated deliverable contracts and ensured timely fulfillment of branding and stage visibility commitments.</li>
        <li>Coordinated cross-functional teams to align sponsor activations with fest event logistics and marketing campaigns.</li>
      </ul>
    </div>
  </div>

</body>
</html>
"""

def run():
    with open(OUTPUT_HTML, 'w', encoding='utf-8') as f:
        f.write(HTML_TEMPLATE)

    print("[NativeCV] Rendering with embedded Computer Modern LaTeX fonts via Playwright...")
    with sync_playwright() as p:
        b = p.chromium.connect_over_cdp('http://127.0.0.1:9230/default/native-cv')
        page = b.contexts[0].new_page()
        page.goto(f'file://{OUTPUT_HTML}', wait_until='load')
        page.wait_for_timeout(1000)

        pdf_bytes = page.pdf(
            format='Letter',
            print_background=True,
            margin={{'top': '0', 'right': '0', 'bottom': '0', 'left': '0'}}
        )
        page.close()

    with open(OUTPUT_PDF, 'wb') as f:
        f.write(pdf_bytes)
    print(f"[NativeCV] Saved to Downloads: {OUTPUT_PDF}")

    with open(REPO_PDF, 'wb') as f:
        f.write(pdf_bytes)
    print(f"[NativeCV] Saved to Repo: {REPO_PDF}")

    reader = pypdf.PdfReader(OUTPUT_PDF)
    print(f"[NativeCV] Page count: {len(reader.pages)}")
    if len(reader.pages) == 1:
        print("[NativeCV] ✅ PERFECT 1-PAGE RESUME!")
    else:
        print(f"[NativeCV] ⚠️ Page count is {len(reader.pages)}, needs adjustment.")

if __name__ == '__main__':
    run()
