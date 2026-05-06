# **0010: Z-Score Normalization for Insight Ranking**

**Context:** During a match, multiple metrics representing completely different units of measurement (e.g., Shooting Percentage, BPM rate, total Saves count) may deviate simultaneously from the user's Historical Baseline. To display the "single most extreme" outlier on the Podium UI, the Engine needs a mathematically sound way to compare apples to oranges.  
**Decision:** The Insight Engine will use Z-scores (Standard Deviations) to normalize all baseline comparisons. The SQLite database will be responsible for maintaining the historical mean and variance for all tracked metrics. When evaluating the Live Match State, the Engine will calculate the Z-score for each metric. The metric with the highest absolute Z-score will be designated the "most extreme."  
**Consequences:** \* **Positive:** It provides a mathematically rigorous, unbiased, and universally comparable scale for all data types.

* **Negative:** It increases the complexity of the SQLite schema and the background data pipeline, as every match insertion must update a rolling variance/standard deviation calculation, not just a simple moving average.