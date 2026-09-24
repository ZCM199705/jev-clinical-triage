# Supplementary Table S2 | Output-format sensitivity

| Experiment | Population | Luna acceptable / valid / planned | Gemini | DeepSeek |
|---|---|---:|---:|---:|
| Structured JSON, first round | 60 main inputs | 44/60/60 | 47/60/60 | 42/60/60 |
| Explanation JSON, one sample | 60 main inputs | 46/60/60 | 42/60/60 | 48/60/60 |
| Natural-language consensus | 60 main + 8 emergency inputs | 31/37/68 | 40/49/68 | 36/41/68 |

Values are acceptable outputs / valid or codable outputs / planned inputs. The natural-language row includes eight emergency inputs per model; it is not directly comparable to the 60-main-input rows. One explanation sample was compared with the first structured round; later baseline rounds and paired differences remain in the original analysis files. Model consensus is an exploratory coding convention, not an independent clinical label. Direction of format sensitivity varies by model.
