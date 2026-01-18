# Microsoft Bookings Automation

[![Python](https://img.shields.io/badge/Python-3.11+-blue.svg?style=for-the-badge)](https://www.python.org/downloads/)
[![Playwright](https://img.shields.io/badge/Playwright-1.40+-green.svg?style=for-the-badge)](https://playwright.dev/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg?style=for-the-badge)](https://opensource.org/licenses/MIT)

A robust **Python automation tool** using **Playwright** to schedule recurring appointments on **Microsoft Bookings** pages. Initially designed for bulk-scheduling Graduate Teaching Assistant (GTA) Office Hours at George Mason University (GMU), this tool can be configured for any Microsoft Bookings service.

> **Key Features:**
>
> - 🗓️ **Bulk Scheduling**: Automate booking for an entire semester or custom date range.
> - 🧩 **Smart Selectors**: Robust element detection that handles dynamic Microsof Bookings structures.
> - ⚙️ **Configurable**: Driven by `.env` variables for easy adaptation to different services/staff.
> - 🛠️ **Dev Tools**: Includes built-in `record` (codegen) and `inspect` modes for selector debugging.
> - 🚀 **Headless & Headed**: Run silently in the background or watch the automation for verification.

---

## 🚀 Quick Start

### Prerequisites

- **Python 3.11+**
- **uv** (Modern Python package manager, recommended) or pip

### Installation

1. **Install uv** (if missing):

    ```bash
    curl -LsSf https://astral.sh/uv/install.sh | sh
    ```

2. **Sync Dependencies**:

    ```bash
    uv sync
    ```

3. **Install Browser Engines**:
    ```bash
    uv run playwright install chromium
    ```

---

## ⚙️ Configuration

1. **Create Environment File**:

    ```bash
    cp .env.example .env
    ```

2. **Update Settings** (`.env`):

    ```bash
    # Booking Page URL
    BOOKING_URL=https://outlook.office365.com/book/YOUR_SERVICE_PAGE

    # Service Details (Exact text match or partial)
    BOOKING_SERVICE="Office Hours 2 Hours"
    BOOKING_STAFF="ENGR 4456 D7"
    BOOKING_TIME_SLOT="2:00 PM"

    # User Info
    USER_NAME="Jane Doe"
    USER_EMAIL="jdoe@example.edu"

    # Schedule Range
    SEMESTER_START_DATE="2026-01-23"
    SEMESTER_END_DATE="2026-05-01"
    SKIP_DATES="2026-03-13" # Comma-separated (e.g. Spring Break)
    ```

---

## 📚 Usage

### 1. Verification

List all dates that will be targeted based on your configuration.

```bash
uv run main.py list-dates
```

### 2. Dry Run

Simulate the booking loop without submitting.

```bash
uv run main.py book-all --dry-run
```

### Book all Fridays (Parallel)

By default, the script runs with 4 parallel workers to speed up booking.

```bash
uv run main.py book-all
```

To change the number of workers:

```bash
uv run main.py book-all --workers 8
```

### Book with visible browser (Sequential)

Headed mode usually works best sequentially for debugging, but parallel headed is supported (many windows will open).

```bash
uv run main.py book-all --headed --workers 1
```

### 3. Book a Single Date

Test the flow on one specific day (recommended first step).

```bash
uv run main.py book-single 2026-01-23 --headed
```

### 4. Bulk Automate

Book the entire semester in one go.

```bash
uv run main.py book-all
```

---

## 🐞 Debugging & Selector Discovery

Microsoft Bookings pages can change. If the automation fails to find an element:

**Interactive Recorder (Codegen)**
Launches a browser that records your clicks and generates valid Playwright selectors for your specific page. Use this to update `booker.py` if needed.

```bash
uv run main.py record
```

**Manual Inspector**
Opens the booking page in a headed browser with DevTools available for manual inspection.

```bash
uv run main.py inspect
```

---

## 📝 GitHub Metadata

**Description:**
Automate recurring Microsoft Bookings appointments using Python and Playwright. Features bulk scheduling, smart selector handling, and environment-based configuration. Ideal for office hours, recurring meetings, and university scheduling.

**Topics:**
`python`, `playwright`, `automation`, `microsoft-bookings`, `scheduling`, `selenium-alternative`, `bulk-booking`, `gmu`, `office-hours`, `browser-automation`

---

## License

MIT License - see [LICENSE](LICENSE) for details.
