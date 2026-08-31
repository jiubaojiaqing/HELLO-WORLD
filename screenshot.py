"""用 Selenium + Edge 截图交易复盘界面"""
from selenium import webdriver
from selenium.webdriver.edge.options import Options
from selenium.webdriver.common.by import By
import time

EDGE = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
URL = "http://127.0.0.1:8769"
OUT = r"c:\Users\Administrator\Documents\trae_projects\chan-trading\screenshot.png"

options = Options()
options.add_argument("--headless=new")
options.add_argument("--disable-gpu")
options.add_argument("--window-size=1280,900")
options.binary_location = EDGE

driver = webdriver.Edge(options=options)
try:
    driver.get(URL)
    time.sleep(2)
    
    # 点击"开始模拟"按钮
    btns = driver.find_elements(By.TAG_NAME, "button")
    for btn in btns:
        if "开始模拟" in btn.text:
            btn.click()
            print("Clicked: 开始模拟")
            break
    
    time.sleep(3)
    
    # 反复点击"快进5根"加速模拟
    for i in range(200):
        btns = driver.find_elements(By.TAG_NAME, "button")
        clicked = False
        for btn in btns:
            if "快进5根" in btn.text:
                btn.click()
                clicked = True
                break
        if not clicked:
            body_text = driver.find_element(By.TAG_NAME, "body").text
            if "模拟结果" in body_text or "交易复盘" in body_text:
                print(f"Simulation completed at iteration {i}")
                break
        time.sleep(0.3)
    
    time.sleep(2)
    
    # 点击"交易复盘"按钮
    btns = driver.find_elements(By.TAG_NAME, "button")
    for btn in btns:
        if "交易复盘" in btn.text:
            btn.click()
            print("Clicked: 交易复盘")
            break
    
    time.sleep(3)
    
    # 滚动到图表区域
    chart = driver.find_element(By.ID, "chart")
    driver.execute_script("arguments[0].scrollIntoView({block:'start'});", chart)
    time.sleep(2)
    
    # 截图
    driver.save_screenshot(OUT)
    print(f"Screenshot saved: {OUT}")
    
    # 检查图表状态
    canvas_count = len(driver.find_elements(By.TAG_NAME, "canvas"))
    tv_count = len(driver.find_elements(By.CLASS_NAME, "tv-lightweight-charts"))
    sm_labels = len(driver.find_elements(By.CLASS_NAME, "sm-label"))
    bc_labels = len(driver.find_elements(By.CLASS_NAME, "bc-label"))
    print(f"Canvas count: {canvas_count}")
    print(f"TV charts: {tv_count}")
    print(f"BC labels: {bc_labels}")
    print(f"Summacd labels: {sm_labels}")
    
    # 用 JS 检查标签内容
    js_result = driver.execute_script("""
        var labels = document.querySelectorAll('.sm-label');
        var result = [];
        for(var i=0; i<Math.min(labels.length, 10); i++){
            result.push({id: labels[i].id, text: labels[i].textContent, visible: labels[i].offsetParent !== null});
        }
        return JSON.stringify(result);
    """)
    print(f"Label details: {js_result}")
    
finally:
    driver.quit()
