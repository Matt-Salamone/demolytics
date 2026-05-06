# **0017: UI Domain Boundaries and Data Delegation**

**Context:** The application maintains an ever-growing SQLite database of historical matches. While the top 50 matches are queried for the Historical Baseline, older matches become inaccessible "Dark Data." Re-implementing the V1 app's complex historical charts locally would cause feature bloat and distract from the V2 overlay philosophy, especially since the app already automates replay uploads to ballchasing.com for deep analysis. However, local relational features like "Encounter History" (tracking past teammates/opponents) are not supported by third-party tools and must remain local.  
**Decision:** We will strictly enforce UI Domain Boundaries.

1. **The Game Bar Overlay** will remain exclusively transient, rendering only the current Match, the Session Recap, and the 50-game baseline Insights.  
2. **The System Tray Dashboard** (a standard desktop web window) will act as the home for exploring Dark Data, specifically housing the Encounter History search UI and application settings.  
3. **Data Delegation:** We formally delegate deep historical performance charting to ballchasing.com via our automated upload feature, and will not rebuild those charts locally.

**Consequences:** The overlay code remains incredibly lightweight. The SQLite schema is utilized to its full potential (relational Encounter querying) without demanding a massive local frontend to chart it. The user gets the best of both worlds without the UI bloat of V1.