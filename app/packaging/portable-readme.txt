AI Job Search - local browser application

1. Extract the complete AIJobSearch folder to a local, writable disk.
2. Double-click AIJobSearch.exe. The interface opens in your default browser.
3. Keep the small launcher open. Reopen returns to the website; Quit stops work.
   Do not move the executable out of its folder or remove _internal.
4. Follow the website's setup steps. Your workspace is separate from the app:
   %LOCALAPPDATA%\AIJobSearch\workspace

No Python, Node, terminal, user account, or always-on service is required for
normal use. The backend listens only on this computer. This is not a hosted
website; other computers cannot reach it.

Claude assistance requires an Anthropic API key, separate API billing, network
access, and your data-sharing consent. Enter the key in Settings, not in a file.
API keys stay in Windows Credential Manager and are excluded from backups.
Local records and existing PDFs remain available without AI or a connection.

Settings offers consent-based setup for Bun and document dependencies.
Downloads can be large. Compilation needs MiKTeX with LuaLaTeX/XeLaTeX and the
stock-template packages, plus Poppler. No paid AI or installer runs on startup.
Freehire and opt-in LinkedIn have bundled source adapters; Bun runs them after
guided setup. Other optional portal adapters are not in this portable build.
Pasting a full job description does not require a portal runtime.

The app prepares documents; it never submits applications to an employer.
Review PDFs yourself, submit externally, then record the real submission date.

Your data remains in the workspace when the application folder is replaced or
removed. Use Settings to export a backup before updating. Backups contain
career information; choose a passphrase if encryption is needed.

This is an unsigned development build, not a clean-machine-certified release.
Live paid AI, real TeX setup, and novice usability require release validation.
Windows may display a publisher warning. Verify the distributor and archive
checksum; do not disable Windows security protections.

Project license: LICENSE.txt.
Third-party notices: _internal\licenses\index.txt and adjacent license files.
The bundled SDK runtime, PDFium, fonts, Python and Tcl/Tk retain their notices.
Provider/service terms apply separately. This is not an Anthropic product.
