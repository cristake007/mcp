"""Real Chromium fixtures, no public network required."""
import asyncio
from playwright.async_api import async_playwright
from renderer import CHECK, inspect, resource_impact
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
    # Regression: exact auxiliary script cannot invalidate computed CSS;
    # other local scripts, missing styles and challenge pages still do.
    url = "https://tuvkarpat.ro/cursuri/test/"
    jsd = "/cdn-cgi/challenge-platform/scripts/jsd/main.js"
    assert resource_impact("https://tuvkarpat.ro" + jsd + "?v=1", "script", "fetch_failed") == "auxiliary_cloudflare_jsd"
    assert resource_impact("https://tuvkarpat.ro" + jsd, "document", "fetch_failed") == "critical"
    assert resource_impact("https://tuvkarpat.ro" + jsd + ".other", "script", "fetch_failed") == "critical"
    assert resource_impact("https://tuvkarpat.ro/wp-content/elementor.js", "script", "fetch_failed") == "critical"

    def fixture(script=jsd, css=False, challenge=False):
        if challenge:
            html = '<html><head><title>Just a moment...</title></head><body><form id="challenge-form">Checking your browser</form></body></html>'
        else:
            html = '<html><head>' + ('<link rel="stylesheet" href="/missing.css">' if css else '') + '</head><body><div style="display:none">Reducere 20%</div><p>270 euro</p><script src="' + script + '"></script></body></html>'
        def fetch(target):
            if target == url:
                return 200, {"content-type": "text/html"}, html.encode()
            raise RuntimeError("fixture resource unavailable")
        return fetch

    with patch("renderer.gateway", side_effect=fixture()):
        result = await inspect(url, ["Reducere 20%", "270 euro"], "desktop")
        assert result["complete"], result
        assert [m["verdict"] for m in result["matches"]] == ["hidden", "visible"], result
        assert result["resources"]["auxiliary_failures"] == 1, result
        assert result["resources"]["critical_failures"] == 0, result
        assert any(w["impact"] == "auxiliary_cloudflare_jsd" for w in result["resource_warnings"]), result
    for fetch in (fixture(script="/elementor.js"), fixture(css=True), fixture(challenge=True)):
        with patch("renderer.gateway", side_effect=fetch):
            result = await inspect(url, ["Reducere 20%"], "desktop")
            assert not result["complete"], result
            assert result["matches"][0]["verdict"] == "indeterminate", result
    print("PASS: real Chromium CSS visibility, auxiliary resources, challenge documents and gateway allowlist")


if __name__=="__main__":
    asyncio.run(main())
