## Compare SQLite's write-ahead-log (WAL) journal mode with its default rollback-journal (DELETE) mode

Using the official SQLite documentation (sqlite.org), answer: (1) how each mode treats a reader that is active while a writer commits, (2) what each mode requires or forbids about the filesystem the database lives on, and (3) which mode is the default and how to switch with a PRAGMA. Cite a URL for every claim and say which page you read it on. If a point cannot be confirmed from the official documentation, say so instead of guessing.

Write your findings to FINDINGS.md in the working directory. Lead with a three-line answer (one line per question), then a short evidence list of URL plus the exact quoted text that supports each line. Do not write anything else to that directory.

## Where a usage-limit stop is detected in this repository

Answer from this repository's code and docs only: when a worker hits a subscription usage limit, how does the engine find out, what does it do with the phase's attempt count, and where is the reset time recorded? Name the function or code path that recognises the limit for each of the two worker command lines, the journal event or state value the engine writes when it stops, and the document that describes the behaviour. Cite every claim as `path:line`, and open each cited file to confirm the line number before you write it.

Write your findings to FINDINGS.md in the working directory. Lead with a short direct answer to the three questions, then the `path:line` citations. Do not write anything else to that directory.

## Does this repository's browser bridge use the Chrome DevTools Protocol methods as the protocol documents them

The browser bridge in this repository drives a browser over the Chrome DevTools Protocol. First, in `pipeline/browser.py`, find the protocol methods it calls to open a tab, navigate it, run script in the page, and close the tab, and the events it waits for after navigating; cite `path:line` for each. Second, using the official Chrome DevTools Protocol documentation (chromedevtools.github.io/devtools-protocol), confirm for each method which domain it belongs to and which parameters it takes, and cite the URL. Third, state whether the repository passes any parameter the documentation does not list for that method, and whether any method it calls is marked deprecated or experimental. If the official page does not settle a point, say so.

Write your findings to FINDINGS.md in the working directory. Lead with the answer: a one-paragraph verdict of match or mismatch with any differing method or parameter named, then the repository citations, then the web citations with quoted text. Do not write anything else to that directory.
