import os
import sys
import pypdf
from playwright.sync_api import sync_playwright

OUTPUT_HTML = '/tmp/resume_render.html'
OUTPUT_PDF = os.path.expanduser('~/Downloads/Vishal_Gupta_Resume.pdf')
PROJECT_PDF = '/Users/admin/linkedin-multiagent/data/resume.pdf'
OUTPUT_TEX = os.path.expanduser('~/Downloads/Vishal_Gupta_Resume.tex')

HTML_CONTENT = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<title>Vishal Gupta - Resume</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Cinzel:wght@600;700&family=EB+Garamond:ital,wght@0,400;0,500;0,600;0,700;1,400;1,500&display=swap" rel="stylesheet">
<link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css">
<style>
  @page {
    size: letter;
    margin: 0;
  }
  * {
    box-sizing: border-box;
    margin: 0;
    padding: 0;
  }
  body {
    font-family: 'EB Garamond', Garamond, 'Times New Roman', serif;
    color: #111111;
    background-color: #ffffff;
    font-size: 9.7pt;
    line-height: 1.22;
    padding: 0.30in 0.44in;
    width: 8.5in;
    margin: 0 auto;
    -webkit-font-smoothing: antialiased;
  }

  /* Header */
  .header {
    text-align: center;
    margin-bottom: 0.08in;
  }
  .name {
    font-size: 20pt;
    font-weight: 700;
    letter-spacing: 0.5px;
    color: #0f172a;
    text-transform: uppercase;
    margin-bottom: 2px;
  }
  .contact-bar {
    font-size: 9.0pt;
    color: #334155;
    display: flex;
    justify-content: center;
    align-items: center;
    flex-wrap: wrap;
    gap: 10px;
  }
  .contact-item {
    display: inline-flex;
    align-items: center;
    gap: 4px;
    color: #334155;
    text-decoration: none;
  }
  .contact-item i {
    font-size: 8pt;
    color: #475569;
  }
  .contact-item a {
    color: #1e3a8a;
    text-decoration: none;
  }
  .contact-item a:hover {
    text-decoration: underline;
  }
  .sep {
    color: #94a3b8;
  }

  /* Section Styles */
  .section {
    margin-bottom: 0.08in;
  }
  .section-title {
    font-size: 10.2pt;
    font-weight: 700;
    text-transform: uppercase;
    letter-spacing: 0.8px;
    color: #0f172a;
    border-bottom: 1.2px solid #1e293b;
    padding-bottom: 1px;
    margin-bottom: 4px;
  }

  /* Item Styles */
  .item {
    margin-bottom: 4px;
  }
  .item:last-child {
    margin-bottom: 0;
  }
  .item-header {
    display: flex;
    justify-content: space-between;
    align-items: baseline;
    margin-bottom: 1px;
  }
  .item-title-left {
    font-size: 10.1pt;
    font-weight: 700;
    color: #0f172a;
  }
  .item-title-right {
    font-size: 9.1pt;
    font-style: italic;
    color: #475569;
  }
  .item-sub-left {
    font-size: 9.4pt;
    font-style: italic;
    color: #334155;
  }
  .item-sub-right {
    font-size: 8.8pt;
    color: #64748b;
  }

  /* Bullet points */
  ul.bullets {
    list-style-type: disc;
    margin-left: 14px;
    margin-top: 1px;
  }
  ul.bullets li {
    font-size: 9.1pt;
    line-height: 1.20;
    color: #1e293b;
    margin-bottom: 1.5px;
    padding-left: 1px;
    text-align: justify;
  }
  ul.bullets li strong {
    font-weight: 600;
    color: #0f172a;
  }

  /* Skills Grid */
  .skills-list {
    font-size: 9.1pt;
    line-height: 1.28;
    color: #1e293b;
  }
  .skills-list p {
    margin-bottom: 1.5px;
  }
  .skills-list strong {
    font-weight: 700;
    color: #0f172a;
  }
</style>
</head>
<body>

  <!-- HEADER -->
  <div class="header">
    <div class="name">Vishal Gupta</div>
    <div class="contact-bar">
      <span class="contact-item"><i class="fa-solid fa-phone"></i> +91 8368387245</span>
      <span class="sep">|</span>
      <span class="contact-item"><i class="fa-solid fa-envelope"></i> <a href="mailto:vishal90301@gmail.com">vishal90301@gmail.com</a></span>
      <span class="sep">|</span>
      <span class="contact-item"><i class="fa-brands fa-linkedin"></i> <a href="https://www.linkedin.com/in/vshal-g007" target="_blank">linkedin.com/in/vshal-g007</a></span>
      <span class="sep">|</span>
      <span class="contact-item"><i class="fa-brands fa-github"></i> <a href="https://github.com/vishal-g17" target="_blank">github.com/vishal-g17</a></span>
      <span class="sep">|</span>
      <span class="contact-item"><i class="fa-solid fa-code"></i> <a href="https://codolio.com/profile/GtjCJNug" target="_blank">Coding Portfolio</a></span>
    </div>
  </div>

  <!-- EDUCATION -->
  <div class="section">
    <div class="section-title">Education</div>
    <div class="item">
      <div class="item-header">
        <span class="item-title-left">Netaji Subhas University of Technology (NSUT), Delhi</span>
        <span class="item-title-right">2022 – 2026</span>
      </div>
      <div class="item-header">
        <span class="item-sub-left">B.Tech in Computer Science & Engineering (Specialization in Mathematics & Computing)</span>
        <span class="item-sub-right">New Delhi, India</span>
      </div>
    </div>
  </div>

  <!-- WORK EXPERIENCE -->
  <div class="section">
    <div class="section-title">Work Experience</div>

    <!-- Magicpin -->
    <div class="item">
      <div class="item-header">
        <span class="item-title-left">Magicpin</span>
        <span class="item-title-right">Aug’26 – Present</span>
      </div>
      <div class="item-header">
        <span class="item-sub-left">Tech Product Analyst</span>
        <span class="item-sub-right">Gurugram, India</span>
      </div>
      <ul class="bullets">
        <li>Built an automated merchant reactivation pipeline for inactive/high-cancellation merchants, <strong>reducing cancellations from 10–12% to 4–6%</strong>.</li>
        <li>Built automated ingestion and pricing pipeline integrating competitor crawlers and POS webhooks to eliminate catalog deficits (items &le; 8, image coverage &lt; 60%); deployed fuzzy dish-matching to enforce 1:1 base MRP parity against benchmarks, eliminating friction for low-CVR merchants (&lt;1%) while protecting discount margins.</li>
        <li>Engineered real-time merchant funnel telemetry (DAU &rarr; Views &rarr; Carts &rarr; Orders); diagnosed low-conversion cohorts (&lt;1% CVR) and resolved listing glitches on premium surfaces (Magic9).</li>
        <li>Conducted merchant-level opportunity analysis across 7 mega cities, mapping locality- and item-level demand/search gaps against inactive supply to drive targeted activation and onboarding.</li>
      </ul>
    </div>

    <!-- Lifelong Online -->
    <div class="item">
      <div class="item-header">
        <span class="item-title-left">Lifelong Online</span>
        <span class="item-title-right">Feb’26 – Aug’26</span>
      </div>
      <div class="item-header">
        <span class="item-sub-left">Business Analyst</span>
        <span class="item-sub-right">Gurugram, India</span>
      </div>
      <ul class="bullets">
        <li>Built an end-to-end ERP automation pipeline (Python, Playwright, pandas) that auto-downloads, merges, and consolidates SKU-level PO/GRN/ATP data &mdash; <strong>eliminating 3&ndash;4 hours of daily manual reporting</strong>.</li>
        <li>Engineered cross-source data pipelines merging 6+ datasets (Item Master, PO files, Blinkit FC, SKU master) with automated audit trails flagging unmapped records for procurement accuracy.</li>
        <li>Developed and deployed an internal Sticker Generator web platform from scratch, enabling business teams to self-serve custom product sticker creation.</li>
        <li>Built automated quick-commerce competitive pricing scraper using Python and Playwright to extract real-time product prices.</li>
      </ul>
    </div>

    <!-- NOM -->
    <div class="item">
      <div class="item-header">
        <span class="item-title-left">NOM</span>
        <span class="item-title-right">May’25 – Nov’25</span>
      </div>
      <div class="item-header">
        <span class="item-sub-left">Founding Product Manager</span>
        <span class="item-sub-right">Remote</span>
      </div>
      <ul class="bullets">
        <li>Conducted market research and competitor benchmarking to inform product strategy and validate core features during early-stage development.</li>
        <li>Designed product flow architecture integrating LLM capabilities for scalable feature development; owned QA testing and collaborated with developers to iterate and refine features.</li>
        <li>Architected LLM workflows, structured prompt engineering, and automated evaluation pipelines to improve output accuracy and coverage.</li>
      </ul>
    </div>
  </div>

  <!-- PROJECTS -->
  <div class="section">
    <div class="section-title">Projects</div>

    <div class="item">
      <div class="item-header">
        <span class="item-title-left">Autonomous Multi-Agent Career & Outreach Engine</span>
        <span class="item-title-right">Python, Playwright, SQLite, Discord API, Gemini LLM</span>
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
    <div class="skills-list">
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
      <div class="item-header">
        <span class="item-title-left">Moksha Innovision 2024 (NSUT Annual Cultural Fest)</span>
        <span class="item-title-right">Dec’23 – Mar’24</span>
      </div>
      <div class="item-header">
        <span class="item-sub-left">Sponsorship & Collaboration Lead</span>
        <span class="item-sub-right">New Delhi, India</span>
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

LATEX_CONTENT = r"""\documentclass[letterpaper,10pt]{article}

\usepackage{latexsym}
\usepackage[empty]{fullpage}
\usepackage{titlesec}
\usepackage{marvosym}
\usepackage[usenames,dvipsnames]{color}
\usepackage{verbatim}
\usepackage{enumitem}
\usepackage[hidelinks]{hyperref}
\usepackage{fancyhdr}
\usepackage[english]{babel}
\usepackage{tabularx}
\usepackage{fontawesome5}

\pagestyle{fancy}
\fancyhf{} % clear all header and footer fields
\fancyfoot{}
\renewcommand{\headrulewidth}{0pt}
\renewcommand{\footrulewidth}{0pt}

% Adjust margins
\addtolength{\oddsidemargin}{-0.5in}
\addtolength{\evensidemargin}{-0.5in}
\addtolength{\textwidth}{1in}
\addtolength{\topmargin}{-.5in}
\addtolength{\textheight}{1.0in}

\urlstyle{same}

\raggedbottom
\raggedright
\setlength{\tabcolsep}{0in}

% Sections formatting
\titleformat{\section}{
  \vspace{-4pt}\scshape\raggedright\large
}{}{0em}{}[\color{black}\titlerule \vspace{-5pt}]

% Custom commands
\newcommand{\resumeItem}[1]{
  \item\small{
    {#1 \vspace{-2pt}}
  }
}

\newcommand{\resumeSubheading}[4]{
  \vspace{-2pt}\item
    \begin{tabular*}{0.97\textwidth}[t]{l@{\extracolsep{\fill}}r}
      \textbf{#1} & #2 \\
      \textit{\small#3} & \textit{\small #4} \\
    \end{tabular*}\vspace{-7pt}
}

\newcommand{\resumeProjectHeading}[2]{
    \item
    \begin{tabular*}{0.97\textwidth}{l@{\extracolsep{\fill}}r}
      \small#1 & #2 \\
    \end{tabular*}\vspace{-7pt}
}

\renewcommand\labelitemii{$\vcenter{\hbox{\tiny$\bullet$}}$}

\newcommand{\resumeSubHeadingListStart}{\begin{itemize}[leftmargin=0.15in, label={}]}
\newcommand{\resumeSubHeadingListEnd}{\end{itemize}}
\newcommand{\resumeItemListStart}{\begin{itemize}[leftmargin=0.15in]}
\newcommand{\resumeItemListEnd}{\end{itemize}\vspace{-5pt}}

%-------------------------------------------
%%%%%%  RESUME STARTS HERE  %%%%%%%%%%%%%%%%%%%%%%%%%%%%

\begin{document}

%----------HEADING----------
\begin{center}
    \textbf{\Huge \scshape Vishal Gupta} \\ \vspace{3pt}
    \small \faPhone* \ +91 8368387245 \ $|$ \ 
    \href{mailto:vishal90301@gmail.com}{\faEnvelope \ vishal90301@gmail.com} \ $|$ \ 
    \href{https://www.linkedin.com/in/vshal-g007}{\faLinkedin \ linkedin.com/in/vshal-g007} \ $|$ \ 
    \href{https://github.com/vishal-g17}{\faGithub \ github.com/vishal-g17} \ $|$ \ 
    \href{https://codolio.com/profile/GtjCJNug}{\faCode \ Coding Portfolio}
\end{center}

%-----------EDUCATION-----------
\section{Education}
  \resumeSubHeadingListStart
    \resumeSubheading
      {Netaji Subhas University of Technology (NSUT)}{New Delhi, India}
      {B.Tech in Computer Science \& Engineering (Specialization in Mathematics \& Computing)}{2022 -- 2026}
  \resumeSubHeadingListEnd

%-----------EXPERIENCE-----------
\section{Work Experience}
  \resumeSubHeadingListStart

    \resumeSubheading
      {Magicpin}{Gurugram, India}
      {Tech Product Analyst}{Aug'26 -- Present}
      \resumeItemListStart
        \resumeItem{Built an automated merchant reactivation pipeline for inactive/high-cancellation merchants, \textbf{reducing cancellations from 10--12\% to 4--6\%}.}
        \resumeItem{Built automated ingestion and pricing pipeline integrating competitor crawlers and POS webhooks to eliminate catalog deficits (items $\le$ 8, image coverage $<$ 60\%); deployed fuzzy dish-matching to enforce 1:1 base MRP parity against competitor benchmarks, eliminating friction for low-CVR merchants ($<$1\%) while protecting platform discount layers.}
        \resumeItem{Engineered real-time merchant funnel telemetry (DAU $\rightarrow$ Views $\rightarrow$ Carts $\rightarrow$ Orders); diagnosed low-conversion cohorts ($<$1\% CVR) and resolved listing glitches on premium surfaces (Magic9).}
        \resumeItem{Conducted merchant-level opportunity analysis across 7 mega cities, mapping locality- and item-level demand/search gaps against inactive supply to drive targeted activation and onboarding.}
      \resumeItemListEnd

    \resumeSubheading
      {Lifelong Online}{Gurugram, India}
      {Business Analyst}{Feb'26 -- Aug'26}
      \resumeItemListStart
        \resumeItem{Built an end-to-end ERP automation pipeline (Python, Playwright, pandas) that auto-downloads, merges, and consolidates SKU-level PO/GRN/ATP data --- \textbf{eliminating 3--4 hours of daily manual reporting}.}
        \resumeItem{Engineered cross-source data pipelines merging 6+ datasets (Item Master, PO files, Blinkit FC, SKU master) with audit trails flagging unmapped records for procurement accuracy.}
        \resumeItem{Developed and deployed an internal Sticker Generator platform from scratch, enabling business teams to self-serve custom product sticker creation.}
        \resumeItem{Built an automated quick-commerce competitive pricing scraper using Python and Playwright to extract real-time product prices.}
      \resumeItemListEnd

    \resumeSubheading
      {NOM}{Remote}
      {Founding Product Manager}{May'25 -- Nov'25}
      \resumeItemListStart
        \resumeItem{Conducted market research and competitor benchmarking to inform product strategy and validate key features during early-stage development.}
        \resumeItem{Designed product flow architecture integrating LLM capabilities for scalable feature development; owned QA testing and collaborated with developers to refine features.}
        \resumeItem{Architected LLM workflows, structured prompt engineering, and automated evaluation pipelines to improve output accuracy and coverage.}
      \resumeItemListEnd

  \resumeSubHeadingListEnd

%-----------PROJECTS-----------
\section{Projects}
    \resumeSubHeadingListStart
      \resumeProjectHeading
          {\textbf{Autonomous Multi-Agent Career \& Outreach Engine} $|$ \emph{Python, Playwright, SQLite, Discord API, Gemini LLM}}{2026}
          \resumeItemListStart
            \resumeItem{\textbf{Distributed Multi-Agent Architecture:} Architected a production-grade multi-agent system orchestrating 4 specialized autonomous workers (DOM Form Filler, Lead Enrichment Engine, DNS/SMTP Verifier, and Discord Remote Controller).}
            \resumeItem{\textbf{Application Automation Pipeline:} Built an autonomous job application pipeline using Playwright CDP that dynamically solves multi-step forms, handles contenteditable screening chatbots, and navigates external career redirects across Naukri, Indeed, and Google Forms --- \textbf{submitting 100+ applications autonomously}.}
            \resumeItem{\textbf{Pipeline \& Anti-Bot Engineering:} Designed transactional SQLite queue pipelines processing 4,000+ candidate profiles with stealth CDP injection, humanized mouse-wheel physics, and velocity throttles to bypass anti-bot detections.}
            \resumeItem{\textbf{Remote Command Center:} Integrated a conversational Discord bot daemon over WebSockets enabling real-time mobile telemetry, asynchronous shell execution, and live pipeline control.}
          \resumeItemListEnd
    \resumeSubHeadingListEnd

%-----------TECHNICAL SKILLS-----------
\section{Technical Skills}
 \begin{itemize}[leftmargin=0.15in, label={}]
    \small{\item{
     \textbf{Programming \& Languages}{: Python, SQL, C++, C, JavaScript, Bash} \\
     \textbf{Tools \& Frameworks}{: Playwright, SQLite, Git, Discord API, Cloudflare Tunnels, Pandas, Linux Daemons, Postman} \\
     \textbf{AI \& Automation}{: Multi-Agent Systems, Agentic Workflows, Google Gemini API, Prompt Engineering, Web Automation} \\
     \textbf{Core Competencies}{: Product Analytics, Funnel Telemetry, Conversion Rate Optimization (CRO), ERP Automation, System Design}
    }}
 \end{itemize}

%-----------POSITIONS OF RESPONSIBILITY-----------
\section{Positions of Responsibility}
  \resumeSubHeadingListStart
    \resumeSubheading
      {Moksha Innovision 2024 (NSUT Annual Cultural Fest)}{New Delhi, India}
      {Sponsorship \& Collaboration Lead}{Dec'23 -- Mar'24}
      \resumeItemListStart
        \resumeItem{Secured monetary and in-kind corporate sponsorships; negotiated deliverable contracts and ensured timely fulfillment of branding and stage visibility commitments.}
        \resumeItem{Coordinated cross-functional teams to align sponsor activations with fest event logistics and marketing campaigns.}
      \resumeItemListEnd
  \resumeSubHeadingListEnd

\end{document}
"""

def generate():
    # 1. Write HTML
    with open(OUTPUT_HTML, 'w', encoding='utf-8') as f:
        f.write(HTML_CONTENT)

    # 2. Write LaTeX
    with open(OUTPUT_TEX, 'w', encoding='utf-8') as f:
        f.write(LATEX_CONTENT)
    print(f"[CVGenerator] Wrote Overleaf LaTeX source to: {OUTPUT_TEX}")

    # 3. Render via Chrome CDP
    print("[CVGenerator] Connecting to Chrome CDP to compile pixel-perfect PDF...")
    with sync_playwright() as p:
        b = p.chromium.connect_over_cdp('http://127.0.0.1:9230/default/cv-compiler')
        context = b.contexts[0]
        page = context.new_page()
        page.goto(f'file://{OUTPUT_HTML}', wait_until='networkidle', timeout=15000)
        page.wait_for_timeout(1000)

        # Print to PDF with exact Letter format and zero margins (CSS controlled)
        pdf_bytes = page.pdf(
            format='Letter',
            print_background=True,
            margin={'top': '0', 'right': '0', 'bottom': '0', 'left': '0'}
        )
        page.close()

    # Save to Downloads
    with open(OUTPUT_PDF, 'wb') as f:
        f.write(pdf_bytes)
    print(f"[CVGenerator] Saved updated PDF to: {OUTPUT_PDF}")

    # Save to repo
    os.makedirs(os.path.dirname(PROJECT_PDF), exist_ok=True)
    with open(PROJECT_PDF, 'wb') as f:
        f.write(pdf_bytes)
    print(f"[CVGenerator] Updated repo PDF at: {PROJECT_PDF}")

    # Verify Page Count
    reader = pypdf.PdfReader(OUTPUT_PDF)
    num_pages = len(reader.pages)
    print(f"[CVGenerator] Verified Page Count: {num_pages} Page(s)")
    if num_pages == 1:
        print("[CVGenerator] ✅ PERFECT 1-PAGE RESUME CONFIRMED!")
    else:
        print(f"[CVGenerator] ⚠️ Warning: Generated {num_pages} pages instead of 1.")

if __name__ == '__main__':
    generate()
