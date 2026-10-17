# Live smoke test (2026-10-17 20:19 CST)

- DeepSeek API /models: ok=True missing=[] notes=[] error=''
- NVIDIA models visible: none
- fast deepseek-flash: mode=live 1348 ms usage={'prompt_tokens': 81, 'completion_tokens': 71, 'total_tokens': 152, 'prompt_cache_hit_tokens': 0, 'prompt_cache_miss_tokens': 81} data={"ok": true, "model_tier": "fast"} 
- main deepseek-flash: mode=live 1283 ms usage={'prompt_tokens': 81, 'completion_tokens': 44, 'total_tokens': 125, 'prompt_cache_hit_tokens': 0, 'prompt_cache_miss_tokens': 81} data={"ok": true, "model_tier": "main"} 
- deep deepseek-v4-pro: mode=live 2948 ms usage={'prompt_tokens': 132, 'completion_tokens': 115, 'total_tokens': 247, 'prompt_cache_hit_tokens': 0, 'prompt_cache_miss_tokens': 132} data={"ok": true, "model_tier": "deep"} 
- Tavily: skipped (no TAVILY_API_KEY)
- PayPal: skipped (no PAYPAL_CLIENT_ID/SECRET)
