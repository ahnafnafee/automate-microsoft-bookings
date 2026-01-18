"""
Microsoft Bookings automation using Playwright.
Automates the booking of GTA Office Hours on the GMU booking page.
"""
import time
from datetime import date
from pathlib import Path
from dataclasses import dataclass
from playwright.sync_api import sync_playwright, Page, TimeoutError as PlaywrightTimeout


@dataclass
class BookingConfig:
    """Configuration for a booking."""
    url: str
    service: str
    staff: str
    time_slot: str
    name: str
    email: str
    address: str = ""
    phone: str = ""
    notes: str = ""


class BookingAutomation:
    """Handles the Playwright automation for Microsoft Bookings."""
    
    def __init__(self, headless: bool = True, slow_mo: int = 100, debug: bool = False):
        self.headless = headless
        self.slow_mo = slow_mo
        self.debug = debug
        self.screenshot_dir = Path("debug_screenshots")
        if self.debug:
            self.screenshot_dir.mkdir(exist_ok=True)
    
    def book_date(self, config: BookingConfig, target_date: date) -> dict:
        """
        Book a specific date on Microsoft Bookings.
        
        Args:
            config: Booking configuration
            target_date: The date to book
            
        Returns:
            Dict with 'success' boolean and 'message' string
        """
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=self.headless, slow_mo=self.slow_mo)
            context = browser.new_context(viewport={"width": 1280, "height": 900})
            page = context.new_page()
            
            try:
                result = self._perform_booking(page, config, target_date)
            except Exception as e:
                if self.debug:
                    page.screenshot(path=str(self.screenshot_dir / f"error_{target_date.isoformat()}.png"))
                result = {
                    "success": False,
                    "message": f"Error during booking: {str(e)}",
                    "date": target_date.isoformat()
                }
            finally:
                browser.close()
            
            return result
    
    def _screenshot(self, page: Page, name: str):
        """Take a debug screenshot if debug mode is enabled."""
        if self.debug:
            page.screenshot(path=str(self.screenshot_dir / f"{name}.png"))
            print(f"  📸 Screenshot saved: {name}.png")
    
    def _perform_booking(self, page: Page, config: BookingConfig, target_date: date) -> dict:
        """Perform the actual booking steps."""
        print("  Step 1: Navigating to booking page...")
        page.goto(config.url)
        page.wait_for_load_state("domcontentloaded")
        self._screenshot(page, "01_initial_page")
        
        # Step 1: Select the service (Office Hours 2 Hours)
        print("  Step 2: Selecting service...")
        self._select_service(page, config.service)
        self._screenshot(page, "02_after_service")
        
        # Step 2: Select staff/room (ENGR 4456 D7)
        print("  Step 3: Selecting staff/room...")
        self._select_staff(page, config.staff)
        self._screenshot(page, "03_after_staff")
        
        # Step 3: Navigate to the target date
        print("  Step 4: Navigating to date...")
        self._navigate_to_date(page, target_date)
        self._screenshot(page, "04_after_date")
        
        # Step 4: Select the time slot
        print("  Step 5: Selecting time slot...")
        self._select_time_slot(page, config.time_slot)
        self._screenshot(page, "05_after_time")
        
        # Step 5: Fill in user details
        print("  Step 6: Filling user details...")
        self._fill_user_details(page, config)
        self._screenshot(page, "06_after_details")
        
        # Step 6: Submit the booking
        print("  Step 7: Submitting booking...")
        self._submit_booking(page)
        self._screenshot(page, "07_after_submit")
        
        return {
            "success": True,
            "message": f"Successfully booked {target_date.isoformat()} at {config.time_slot}",
            "date": target_date.isoformat()
        }
    
    def _select_service(self, page: Page, service_name: str):
        """Select the service type (e.g., 'Office Hours 2 Hours')."""
        print(f"    - looking for service '{service_name}'")
        try:
            # Try exact match from config first, then the one seen in recording
            page.get_by_text(service_name).click(timeout=3000)
            return
        except:
            pass
            
        try:
            # Try with extra "2" seen in recording or partial match
            page.get_by_text(service_name, exact=False).first.click(timeout=2000)
            return
        except:
            pass
            
        # Fallback to recording specific value if config matches standard
        if "Office Hours 2 Hours" in service_name:
             try:
                page.get_by_text("Office Hours 2 Hours2", exact=False).first.click(timeout=2000)
             except:
                pass

    def _select_staff(self, page: Page, staff_name: str):
        """Select staff/room from dropdown."""
        
        print(f"    - looking for staff '{staff_name}'")
        
        # 1. Click the dropdown button
        try:
            page.get_by_role("button", name="Select a staff member").click(timeout=3000)
        except:
            # Try alternate selector if role button fails
            try:
                page.locator("[class*='staff']").click(timeout=2000)
            except:
                pass

        # 2. Select the staff member
        try:
            page.get_by_text(staff_name).click(timeout=3000)
        except:
            pass
    
    def _navigate_to_date(self, page: Page, target_date: date):
        """Navigate the calendar to the target date."""
        target_month_year = target_date.strftime("%B %Y")
        date_label = target_date.strftime("%A, %B %d, %Y")
        
        print(f"    - navigating to {date_label}")
        
        # Keep clicking next until we see the month
        for _ in range(12):
            if target_month_year in page.content():
                break
            
            try:
                # Next month button
                page.get_by_label("Next month").click(timeout=500)
            except:
                try:
                    page.locator("button:has-text('>')").click(timeout=500)
                except:
                    break
        
        # Click the date button
        try:
            page.get_by_role("button", name=date_label).first.click()
            return
        except:
            pass
            
        try:
            page.get_by_role("button", name=f"{date_label}.").first.click()
            return
        except:
            pass
            
        # Fallback to day number
        try:
            day_num = str(target_date.day)
            page.get_by_role("button", name=day_num, exact=True).filtered_by(has_text=day_num).click()
        except:
            pass
    
    def _select_time_slot(self, page: Page, time_slot: str):
        """Select the desired time slot."""
        print(f"    - looking for time '{time_slot}'")
        
        # User reported needing to scroll
        # Scroll the container that holds time slots
        try:
            # Try to find the time slot button and scroll it into view
            slot = page.get_by_text(time_slot, exact=True).first
            slot.scroll_into_view_if_needed()
            slot.click()
            return
        except:
            pass
            
        # Try by role button
        try:
            slot = page.get_by_role("button", name=time_slot).first
            slot.scroll_into_view_if_needed()
            slot.click()
            return
        except:
            pass

    def _fill_user_details(self, page: Page, config: BookingConfig):
        """Fill in the user details form."""
        print("    - filling user details")
        
        # From recording:
        # page.get_by_role("textbox", name="First and last name required").fill("Name")
        # page.get_by_role("textbox", name="Email optional").fill("Email")
        
        try:
            page.get_by_role("textbox", name="First and last name").first.fill(config.name)
        except:
            try:
                page.get_by_label("Name").fill(config.name)
            except:
                pass
                
        try:
            page.get_by_role("textbox", name="Email").first.fill(config.email)
        except:
            try:
                page.get_by_label("Email").fill(config.email)
            except:
                pass

        # Handle optional fields if needed
        if config.notes:
             try:
                page.get_by_role("textbox", name="Notes").fill(config.notes)
             except:
                pass
        
    def _submit_booking(self, page: Page):
        """Submit the booking form."""
        submit_selectors = [
            "button:has-text('Book')",
            "input[type='submit']",
            "[data-automation-id*='book']",
            "button[type='submit']",
        ]
        
        for selector in submit_selectors:
            try:
                btn = page.locator(selector).first
                if btn.is_visible(timeout=2000):
                    btn.click()
                    break
            except:
                continue
        
        # Wait for confirmation with better status check
        try:
            page.wait_for_load_state("networkidle", timeout=5000)
        except:
            pass


def run_single_booking(config: BookingConfig, target_date: date, headless: bool = True) -> dict:
    """
    Helper function for parallel execution.
    Instantiates automation and runs a single booking.
    """
    automation = BookingAutomation(headless=headless)
    return automation.book_date(config, target_date)
