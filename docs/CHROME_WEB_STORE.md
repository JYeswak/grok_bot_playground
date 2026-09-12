# Chrome Web Store release notes for Page to Grok Bot

Use the official Google documentation for the actual release flow:

- <https://developer.chrome.com/docs/webstore/register>
- <https://developer.chrome.com/docs/webstore/publish>
- <https://developer.chrome.com/docs/webstore/cws-dashboard-listing>
- <https://developer.chrome.com/docs/webstore/cws-dashboard-privacy>
- <https://developer.chrome.com/docs/webstore/cws-dashboard-distribution>
- <https://developer.chrome.com/docs/webstore/review-process>
- <https://developer.chrome.com/docs/webstore/program-policies>
- <https://developer.chrome.com/docs/webstore/update>
- <https://chrome.google.com/webstore/devconsole>
- <https://developer.chrome.com/docs/webstore/using-api>
- <https://developer.chrome.com/blog/cws-review-updates-2026>

## Recommended process

1. Register as a Chrome Web Store developer and pay the one-time fee. See the registration guide: <https://developer.chrome.com/docs/webstore/register>.
2. Enable 2-Step Verification on the publisher account. Google requires 2SV, and the publish API documentation calls that out as well: <https://developer.chrome.com/docs/webstore/using-api>.
3. From this repo, run `bash extension/pack.sh`. The upload artifact must be the generated zip with `manifest.json` at the zip root, not inside an `extension/` folder.
4. Open the dashboard at <https://chrome.google.com/webstore/devconsole> and upload the zip as a **New item**.
5. Fill in the listing, privacy, distribution, and test instructions sections using the repository copy in `extension/store/LISTING.md` and `extension/PRIVACY.md`.
6. Submit the draft for review. Review timing changes over time; do not promise approval or a fixed turnaround.
7. Use deferred publish if you want the reviewed item to wait for a later release moment.
8. For the first visibility setting, prefer **Private** or **Unlisted** instead of Public.

## Notes

- The 2026 review update notes that new publishers default to two extension slots: <https://developer.chrome.com/blog/cws-review-updates-2026>.
- The listing, privacy, distribution, and review-process docs are the authoritative sources for what Google asks at submission time.
- Do not claim Google will approve the item. Review is always Google's decision.
