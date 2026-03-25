"""Copyright (c) 2026 Zhi Lin. All rights reserved.
Author: zhi_lin@qq.com
"""

"""Entry point – run with: python run.py"""

import uvicorn

if __name__ == "__main__":
    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
    )
