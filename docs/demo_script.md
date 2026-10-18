# 5-minute demo script (target 4:55)

## Before you record

1. `.env`: set `HANDOFF_AS_OF=2026-10-17` so "overdue" matches the story, and `EMBEDDER=hash`.
2. Fresh state: `python seed.py`, then `python -m ai.extract` (or `python snapshot.py load` if quota is gone).
3. Check the Risks tab. If the data file, timeclock, CFO escalation and Nov 9 risks are all there, you are set.
4. `uvicorn main:app --port 8000`, browser at 1400 px wide or more, zoom 100%, close other tabs.
5. Rehearse once. Do one take of each section if you want to cut them together.
6. Do NOT record against `tests/dev_server_scripted.py`. That data is hand-written, not model output.

## The script

**0:00 to 0:25. Problem.** Open the Documents tab.
"This is a real kind of mess: a contract, two status meetings, an email and two Slack threads for one payroll
implementation. It's Monday, you've just been put on this account. Where do you even start?"

**0:25 to 1:20. Ingest.** Click *Fill with an example update*, then *Add document*. Watch it appear.
"Every document is cleaned, personal data is removed, then an AI reads it against what we already know."
Open the Slack chat and show the before/after: "Someone pasted an employee's SSN, birth date and salary into chat.
That never reaches the model. This is the same idea as the PII step in ADP's own pipeline."

**1:20 to 2:30. Ask.** Back at the top, click *Why did the data file date keep moving?*
"Every answer is cited. These aren't paraphrases, they're verbatim quotes, and the app checks that the quote is really in
the document. If it can't, the claim is thrown away." Click an open document link to show the source.
Optional second question: *Where do we stand on the timeclock API?*

**2:30 to 3:25. Act.** Show the headline, the runway and the board.
"Three commitments are overdue. This one has moved three times: September 22, October 6, October 8, now the 16th.
And this one, the timeclock question, was raised in six documents and nobody ever owned it. A thumbs-up reaction isn't a
commitment." Show *Do this next*, then the Risks tab: "Greg threatening to escalate to the CFO was buried in a chat."

**3:25 to 4:00. Handoff brief.** Click *Write the handoff brief*.
"For the new teammate: what's at risk, who owes what, what's already decided and why. One page, every line traceable."

**4:00 to 4:35. The AI is down.** Click *AI on* so it turns off. Point at the banner. Ask a question: keyword results.
Open the brief again: it still builds. Add a document: it queues. Turn the AI back on and click to process the queue.
"This mirrors how ADP described their own pipeline: the AI is one stage, and everything around it keeps working."

**4:35 to 4:55. Architecture and next.** One slide or a spoken line: documents, clean, redact, chunk, embed, extract,
verified citations, SQLite, views. "Next: Gmail, Slack and Zoom connectors, real authentication, and a named-entity
model for PII."

## If something goes wrong on camera

- AI error or quota: say nothing, flip to the AI-off segment early, and finish. It is a feature.
- Empty board: `python seed.py && python snapshot.py load`, restart the server.
- Wrong day counts: check `HANDOFF_AS_OF` in `.env`.

## Questions judges might ask

- *How accurate is it?* Run `python eval_extraction.py` and quote your real numbers, with the caveat that the sample
  set is small and hand-written.
- *What about real PII?* Pattern-based redaction today. Named-entity detection is the next step. Free-tier AI content may
  be used by Google, so real data would need a paid project.
- *Why not just a chatbot over the documents?* Because the value is the structured memory: owners, due dates, history,
  and what nobody owns. Chat is one view of it.
