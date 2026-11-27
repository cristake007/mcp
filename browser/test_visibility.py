"""Real Chromium fixtures, no public network required."""
import asyncio
from playwright.async_api import async_playwright
from renderer import CHECK
from gateway import validate_url, public_addresses
from unittest.mock import patch


async def main():
    for url in ("http://tuvkarpat.ro/", "https://tuvkarpat.ro.evil.test/",
                "https://tuvkarpat.ro/wp-admin/", "https://127.0.0.1/"):
        try:
            validate_url(url)
        except ValueError:
            pass
        else:
            raise AssertionError(url)
    with patch("gateway.socket.getaddrinfo", return_value=[(2,1,6,"",("127.0.0.1",443))]):
        try:
            public_addresses("tuvkarpat.ro")
        except ValueError:
            pass
        else:
            raise AssertionError("Private DNS accepted")
    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        page = await browser.new_page(viewport={"width":1440,"height":900})
        await page.set_content("""
            <style>@media(max-width:600px){.responsive{display:none}}</style>
            <p>Visible <span>10%</span></p>
            <div style="display:none"><p>Hidden 15%</p></div>
            <div style="opacity:0"><span>Transparent 20%</span></div>
            <p class="responsive">Responsive 25%</p>
            <p>Mixed <span style="display:none">30%</span></p>
            <div style="height:0;overflow:hidden"><p>Clipped 40%</p></div>
        """)
        queries=["Visible 10%","Hidden 15%","Transparent 20%","Responsive 25%","Mixed 30%","Clipped 40%","Missing"]
        async def check():
            r=await page.evaluate(CHECK,{"texts":queries,"includeText":False})
            return [m["observed"] for m in r["matches"]]
        expected=["visible","hidden","hidden","visible","partial","hidden","not_found"]
        assert await check()==expected, await check()
        await page.set_viewport_size({"width":390,"height":844})
        expected[3]="hidden"
        assert await check()==expected, await check()
        await browser.close()
    print("PASS: real Chromium CSS visibility and gateway allowlist")


if __name__=="__main__":
    asyncio.run(main())
