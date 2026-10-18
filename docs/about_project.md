## Inspiration

Implementations fail quietly. Nobody decides to miss a date. A promise made on a call never makes it into a tracker, a question gets asked four times and never gets an owner, and when someone rolls off the account, the reasons behind every decision leave with them. ADP's brief described exactly this: context fragmented across meetings, emails, chats and documents. I wanted a teammate who had read all of it and never forgot a promise.

## What it does

Handoff turns the raw record of a client implementation into a living memory where every claim is cited.

- **It extracts** commitments, decisions, risks and opportunities from meeting transcripts, emails, chat logs and contracts.
- **It shows who owes what** on a board split into "ADP owes", "the client owes" and "nobody owns these yet". Overdue items are flagged, and an item whose due date has moved shows how many times and where it used to be. A runway view plots every open commitment against the go-live date.
- **It answers questions** across all sources, with verbatim quotes as citations. If a quote is not really in the document, the claim is dropped.
- **It suggests what to do next**, and writes a one-page handoff brief for someone joining the account.
- **It keeps decisions with their reasons**, and shows a timeline of everything that changed.
- **It has a second persona.** A client portal shows what each side owes, without ADP's internal risks.
- **It protects personal data.** SSNs, birth dates, pay, employee IDs and personal contact details are removed before any text reaches the AI, and the app shows a before and after.
- **It keeps working when the AI is down.** New documents are still cleaned, redacted, indexed and queued. Search falls back to keywords, and the board and brief are built from stored data with rules. When the AI returns, one click processes the queue.

## How we built it

I built it solo in 24 hours, using Claude as a pair programmer. The backend is Python and FastAPI on SQLite, and the frontend is a single page with no build step.

The pipeline follows the shape ADP described in the workshop: clean the text, remove PII, chunk and embed locally (MiniLM), retrieve to filter noise, then call an LLM (Gemini, free tier) with a static prompt placed first so a provider can reuse its cached prefix.

The key design choice is that the model does not extract a fresh list from each document. It reads documents in date order and is shown the items already tracked, and it answers with create, update or mention operations. That is how a promise made in the kickoff is still there, and gets moved or closed, when the fifth meeting is read. Every operation must carry a quote, and the quote is verified against the document text before anything is stored.

All four item types live in one table with an owner, a side, a due date, and cited sources, plus a change history. The board, risks, decisions, brief and client portal are just different views over it. I wrote 29 automated tests that run against a scripted stand-in for the AI, so the logic could be checked without a key or network.

## Challenges we ran into

- **Trusting the model.** LLMs invent plausible quotes. I verify each quote against the source text and drop anything that fails, so nothing in the product is uncited.
- **Absences are the hard part.** "Someone should check with the vendor" and a thumbs-up are not commitments. The prompt and the cleaning step handle these explicitly, so an item nobody took on stays visibly unowned instead of being quietly assigned.
- **Messy dates.** Chat logs span days, and "by Friday" depends on when it was said. Documents are processed in date order, chat logs are dated by their first message, and the prompt resolves relative dates from the statement rather than from today.
- **Recall on risks.** My first extraction found only 1 of the 7 risks I had planted in the sample data. After revising the prompt with explicit rules for what a risk looks like (a slipping date, a question asked repeatedly with no answer, a frustrated stakeholder, a contract threshold that a discussion could trigger), it found 5 of 7.
- **A tight free tier.** Daily AI quotas are small, so identical requests are cached on disk, extraction is one call per document, and a saved snapshot of a real run can stand in for a live one.
- **Environment friction.** A PyTorch and OpenMP library conflict on macOS cost me time before local embeddings would run.

## Accomplishments that we're proud of

- On my hand-written answer key (21 items), the final run found 16 of the 19 required items: 8 of 9 commitments, 3 of 3 decisions, 4 of 5 required risks and 1 of 2 opportunities. For the commitments it matched, the owner was right 8 of 8 times, the due date 7 of 8 and the status 7 of 8. The key is small and I wrote it myself, so treat this as a guide, not a benchmark.
- Every claim in the product traces back to a verified quote.
- The app still answers, searches, builds a brief and queues new documents with the AI switched off.
- A demo-ready interface where the runway shows each due date and where it used to be, so a slipping commitment is visible at a glance.
- I did it solo, with tests, in 24 hours.

## What we learned

The valuable part of "AI over documents" is not the chat box. It is the structure underneath: owners, dates, history and evidence. Verification mattered more than clever prompting. Telling the model exactly what to look for improved recall far more than I expected. And designing the no-AI path first made the AI part simpler and the demo safer.

## What's next for ADP?

- Connectors for email, Slack and Zoom or Teams, so ingestion is automatic.
- Real authentication and per-client permissions in place of the simulated ADP and client switch.
- Named-entity PII detection (for example Presidio) on top of the pattern rules, since patterns alone miss a stranger's name in free text.
- Entity resolution across people and systems, for a true relationship graph.
- Proactive nudges to the owner of an overdue item, and a portfolio view across all of a manager's clients.
- A larger, independently labeled evaluation set.