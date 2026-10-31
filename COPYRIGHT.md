# Copyright and Licensing Policy

This policy explains who owns the code in this repository, what you may do with it, and the terms for contributing.

## 1. Copyright

Copyright 2026 Muhammad Zain Shahzad and contributors.

"Contributors" means everyone whose contribution has been merged into this repository. Contributors keep the copyright to their own contributions. The project's git history is the record of who contributed what.

## 2. License

The whole project is licensed under the **Apache License, Version 2.0**. The full text is in [LICENSE](LICENSE). Unless a file says otherwise, this covers the backend (`backend/`), the dashboard (`frontend/`), scripts (`scripts/`), documentation (`docs/`, `README.md`, `CLAUDE.md`) and test fixtures.

### What you may do

- Use the software for any purpose: personal, academic, internal or commercial.
- Copy, modify and run it, and build products or services on top of it.
- Distribute the original or modified versions, in source or compiled form, under Apache-2.0 or, for your own modifications, under terms of your choice (see Section 4 of the license).
- Use any patents that contributors hold on their contributions to this project (Section 3 of the license). This grant ends for anyone who sues claiming the project infringes a patent.

### What you must do when you redistribute

- Include a copy of the [LICENSE](LICENSE).
- Keep the existing copyright, patent, trademark and attribution notices.
- Include the [NOTICE](NOTICE) file, or its content, with your distribution.
- Mark the files you changed with a prominent notice that you changed them.

### What is not granted

- **No warranty.** The software is provided "AS IS". The authors are not liable for any damage that results from using it, such as wrong Jira issues, data loss or a missed requirement (Sections 7 and 8 of the license).
- **No trademark rights.** The license does not let you use the project name or the contributors' names to endorse or promote your product (Section 6).

## 3. Contributions

Contributions are welcome through pull requests.

- **Inbound = outbound.** Anything you submit for inclusion is licensed under Apache-2.0, the same terms as the project (Section 5 of the license). There is no separate contributor license agreement.
- **Developer Certificate of Origin.** Each commit must be signed off (`git commit -s`), which adds `Signed-off-by: Your Name <email>`. By signing off you certify the [Developer Certificate of Origin 1.1](https://developercertificate.org/): you wrote the contribution, or you have the right to submit it under an open-source license.
- **Only submit what you have the right to submit.** Do not paste code from sources whose license is incompatible with Apache-2.0 (for example GPL-only code), proprietary code, or code your employer owns unless they permit it.
- **No real client data.** Transcripts, recordings, Jira exports and evaluation fixtures must be synthetic or explicitly de-identified and approved. Never commit secrets, API tokens or `.env` files. A pre-commit hook blocks the most common Atlassian token patterns.
- **AI-assisted contributions** are accepted. The person who submits the contribution is responsible for it and certifies the DCO for it like any other contribution.
- Follow the engineering contract in [CLAUDE.md](CLAUDE.md): tests, client isolation, evidence and policy rules.

## 4. Third-party components

The project depends on open-source packages that `uv` (Python) and `npm` (dashboard) install separately. Their source is not included in this repository. Each one stays under its own license. The main ones are:

| Component | License |
|---|---|
| FastAPI, Pydantic, SQLAlchemy, Alembic, Typer, python-docx, pgvector-python, faster-whisper, React, Vite | MIT |
| httpx, itsdangerous, soundcard | BSD |
| fastembed | Apache-2.0 |
| psycopg 3 | LGPL-3.0 (used as an unmodified, dynamically linked library) |
| PostgreSQL, pgvector extension (Docker image `pgvector/pgvector`) | PostgreSQL License |

Machine-learning models (Whisper, embedding models) are downloaded at runtime from their publishers and come with their own licenses and model cards. If you redistribute a bundle that includes dependencies or models, you must follow their licenses too. To list the installed Python dependencies and their licenses, run `pip-licenses` (or an equivalent tool) inside `backend/.venv`. For the dashboard, use `npx license-checker` in `frontend/`.

External tools and services this project connects to, but does not include or license, have their own terms. These include the Claude Code CLI and the Anthropic API, Atlassian Jira Cloud, Microsoft Teams and Graph, Docker Desktop, and VB-Audio Virtual Cable (donationware, installed separately by the user).

## 5. Trademarks

Claude and Anthropic are trademarks of Anthropic, PBC. Jira and Atlassian are trademarks of Atlassian. Microsoft Teams and Windows are trademarks of Microsoft. Other names belong to their owners. This project names them only to describe what it works with. It is not affiliated with, endorsed by or sponsored by any of them.

## 6. Responsible use

The license does not restrict use, but the platform processes recordings and statements made by real people. If you operate it, you are responsible for getting meeting participants' consent, for complying with recording, privacy and data-protection laws (such as GDPR), and for following the terms of every service you connect it to. The built-in consent records, retention purge and "audio is never stored" design are there to support this, not to replace legal advice.

## 7. Reporting license problems

If you believe something in this repository infringes your copyright or uses a license incorrectly, open an issue on the GitHub repository (or contact the maintainer privately for sensitive matters). The maintainer will review it and remove or fix the material where appropriate.
