import datetime
import multiprocessing as mp
import re
import time
import urllib.parse as parse
from configparser import ConfigParser
from pathlib import Path

import fire
import requests

s = requests.Session()
config = ConfigParser()


def main(url: str):
    if config.has_option("settings", "folder"):
        folder_loc = config["settings"]["folder"]
    else:
        print("no folder location in settings")
        exit()

    o = parse.urlsplit(url)
    file_loc = Path(folder_loc) / get_filename(o, folder_loc)
    start_loc = file_loc.stat().st_size if file_loc.exists() else 0
    start = time.time()
    print(f"下载: {file_loc.name}")
    while "speed_dict" not in locals() or speed_dict["stop"]:
        speed_dict = mp.Manager().dict(
            {
                "size": [0] * 10,
                "time": [time.time()] * 10,
                "total_length": -1,
                "current_length": -2,
                "stop": False,  # 由进度条控制是否重置连接. TODO 0正常, 1速度太慢未考虑proxy, 2速度太慢已考虑proxy
                "max_speed": 0,  # 0表示无法正常连接
            }
        )
        p_progress_bar = mp.Process(target=progress_bar, args=(speed_dict,), daemon=True)
        p_progress_bar.start()

        p_download_file = mp.Process(target=emby_download, args=(url, file_loc, speed_dict), daemon=True)
        p_download_file.start()
        while True:
            if speed_dict["stop"]:  # 速度太慢
                p_download_file.terminate()
                p_download_file.join()
                if p_progress_bar.is_alive():
                    p_progress_bar.terminate()
                    p_progress_bar.join()
                print("\n速度太慢, 5秒后重新连接")
                time.sleep(5)
                break
            elif not p_download_file.is_alive():
                if speed_dict["current_length"] == speed_dict["total_length"] and speed_dict["total_length"] > 0:  # 下载完成
                    p_progress_bar.terminate()
                    p_progress_bar.join()
                    break
                else:  # 下载报错, p_download_file停止
                    if p_progress_bar.is_alive():
                        p_progress_bar.terminate()
                        p_progress_bar.join()
                    print("\n下载失败, 5秒后重新连接")
                    speed_dict["stop"] = True
                    time.sleep(5)
                    break
            time.sleep(1)

        # method_iter_conent(file_loc, response, speed_dict) # 使用iter_content, 无法解决在某时刻没有速度的情况, 且最大速度为1M/s
        # method_shutil(file_loc, response, speed_dict)  # 使用 shutil.copyfileobj, 最快能达8M/s
        # if mp.active_children():
        #     p_progress_bar.terminate()
        #     p_progress_bar.close()
        # else:
        #     print('已经关闭')
        # if speed_dict['stop']:
        #     print('速度太慢, 5秒后重新连接')
        #     time.sleep(5)

    end = time.time()
    print()
    print(f'耗时: {(end - start):.2f}s, 平均速度: {sizeof_fmt((speed_dict["total_length"]-start_loc)/(end - start))}/s\n')


def emby_download(url, file_loc, speed_dict):
    o = parse.urlsplit(url)
    if file_loc.exists():
        start_loc = file_loc.stat().st_size
    else:
        start_loc = 0
    header = {"Accept": "*/*", "Accept-Encoding": "identity;q=1, *;q=0", "Accept-Language": "zh-CN,zh;q=0.9", "Host": o.netloc, "Connection": "keep-alive", "Referer": f"{o.scheme}://{o.netloc}/web/index.html", "User-Agent": "Mozilla/5.0 (Windows NT 10.0; WOW64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/80.0.3987.87 Safari/537.36 SE 2.X MetaSr 1.0", "Range": f"bytes={start_loc}-"} | {"Sec-Fetch-Dest": "video", "Sec-Fetch-Mode": "no-cors", "Set-Fetch-Site": "same-origin"}
    # with s.get(url, headers=header, stream=True, proxies={'http': '127.0.0.1:7890', 'https': '127.0.0.1:7890'}) as response:
    proxies = {"http": "http://127.0.0.1:7890"}
    try:
        with s.get(url, headers=header, stream=True) as response:
            response.raise_for_status()
            total_length = response.headers.get("content-length")
            if total_length is None:  # no content length header
                return response.content

            speed_dict["total_length"] = int(response.headers["Content-Range"].split("/")[1])
            speed_dict["current_length"] = start_loc
            # method_iter_conent(file_loc, response, speed_dict) # 使用iter_content, 无法解决在某时刻没有速度的情况, 且最大速度为1M/s
            method_shutil(file_loc, response, speed_dict)  # 使用 shutil.copyfileobj, 最快能达8M/s
    except Exception as e:
        print(response.headers)
        print("Content-Length", response.headers.get("Content-Length"))
        raise e


def method_shutil(file_loc: Path, response: requests.Response, speed_dict):
    speed_index = 0
    with open(file_loc, "ab") as f:  # https://stackoverflow.com/a/29967714/5340217
        length = 16 * 1024 * 1024
        with memoryview(bytearray(length)) as mv:
            while not speed_dict["stop"]:
                n = response.raw.readinto(mv)
                if not n:
                    break
                elif n < length:
                    with mv[:n] as smv:
                        f.write(smv)

                        speed_index = update_speed(speed_dict, n, speed_index)
                    break
                else:
                    f.write(mv)

                    speed_index = update_speed(speed_dict, n, speed_index)


def method_iter_conent(file_loc, response, speed_dict):
    with open(file_loc, "ab") as f:
        for data in response.iter_content(chunk_size=10 * 1024 * 1024):
            f.write(data)

            update_speed(speed_dict, len(data))


def update_speed(speed_dict, n, speed_index):
    speed_size = speed_dict["size"]
    speed_size[speed_index] = n
    speed_time = speed_dict["time"]
    speed_time[speed_index] = time.time()
    speed_dict["size"] = speed_size  # https://stackoverflow.com/a/37510417/5340217
    speed_dict["time"] = speed_time
    speed_dict["current_length"] += n
    return (speed_index + 1) % 10


def sizeof_fmt(num, suffix="B"):  # https://stackoverflow.com/a/1094933/5340217
    for unit in ["", "k", "M", "G", "T", "P", "E", "Z"]:
        if abs(num) < 1024.0:
            return f"{num:3.2f}{unit}{suffix}"
        num /= 1024.0
    return f"{num:.2f}Y{suffix}"


def get_filename(o: parse.SplitResult, folder_loc: str):
    # return "stream.mkv"
    path_list = o.path.split("/")
    try:
        url = parse.urlunsplit([o.scheme, o.netloc, "/".join(path_list[:2] + ["Users", get_userID(o), "Items"] + [path_list[-2]]), f'X-Emby-Token={parse.parse_qs(o.query)["api_key"][0]}', ""])
        header = {"Connection": "keep-alive", "accept": "application/json", "Sec-Fetch-Dest": "empty", "User-Agent": "Mozilla/5.0 (Windows NT 10.0; WOW64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/80.0.3987.87 Safari/537.36 SE 2.X MetaSr 1.0", "Sec-Fetch-Site": "same-origin", "Sec-Fetch-Mode": "cors", "Referer": f"{o.scheme}://{o.netloc}/web/index.html", "Accept-Language": "zh-CN,zh;q=0.9"}
        # response_json = s.get(url, headers=header).json()
        with s.get(url, headers=header) as response:
            response_json = response.json()
    except:
        print(f'获取文件名失败: {response.text if "response" in locals() else ""}, {url}')
        filename = input("文件名:")
        return filename

    if "SeriesName" in response_json:
        # season = re.findall(r'\d+', response_json['SeasonName'])[0]
        # episode = re.findall(r'\d+', response_json['SortName'])[0]
        season = response_json["ParentIndexNumber"]
        episode = response_json["IndexNumber"]
        filename = f"{response_json['SeriesName']}.S{season:02}E{episode:02}"
    else:
        filename = response_json["Name"]
    if "Container" in response_json:
        filename += "." + response_json["Container"]
    else:
        tmp = response_json["MediaSources"]
        for i in tmp:
            if i["Id"] == parse.parse_qs(o.query)["MediaSourceId"][0]:
                filename += "." + i["Container"]
                break
        else:
            filename += ".mkv"

    # 顺便下载字幕
    showID = path_list[3]
    mediaID = parse.parse_qs(o.query)["MediaSourceId"][0]
    for mediaSource in response_json["MediaSources"]:
        if mediaSource["Id"] == mediaID:
            mediaStreams = mediaSource["MediaStreams"]
            break
    subtitle_index = {"ass": -1, "srt": -1}
    for mediaStream in mediaStreams:
        if mediaStream["IsExternal"]:
            codec = mediaStream["Codec"]
            if codec not in subtitle_index.keys():
                print(f"未知字幕格式: {codec}")
                codec = "other"
                continue
            subtitle_index[codec] = max(subtitle_index[codec], mediaStream["Index"])
    for codec, index in subtitle_index.items():
        if index > -1:
            url = parse.urlunsplit([o.scheme, o.netloc, "/".join(path_list[:2] + ["Videos", showID, mediaID, "Subtitles", str(index), f"Stream.{codec}"]), "", ""])
            print(url)
            with open(Path(folder_loc) / filename.replace(response_json["Container"], codec), "wb") as f:
                f.write(s.get(url, headers=header).content)
            break

    return filename.replace(":", "_")


def get_userID(o: parse.SplitResult):
    # 在*.emby/Users/*这里查看
    # emby/Users/authenticatebyname response['User']['Id']

    key = re.sub(r":.*$", "", o.netloc)
    if config.has_option("account", key):
        return config["account"][key]
    else:
        print(f"访问{o.netloc}并重新运行")
        username = input("username:")
        password = input("password:")
        header = {"Connection": "keep-alive", "accept": "application/json", "User-Agent": "Mozilla/5.0 (Windows NT 10.0; WOW64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/80.0.3987.87 Safari/537.36 SE 2.X MetaSr 1.0", "Sec-Fetch-Site": "same-origin", "Sec-Fetch-Mode": "cors", "Referer": f"{o.scheme}://{o.netloc}/web/index.html", "Accept-Language": "zh-CN,zh;q=0.9"}
        url = parse.urlunsplit([o.scheme, o.netloc, "emby/Users/authenticatebyname", "X-Emby-Client=Emby Web&X-Emby-Device-Name=Chrome&X-Emby-Device-Id=922d8cf2-2d95-4e3a-b918-fd66f18accab&X-Emby-Client-Version=4.6.7.0", ""])
        response = s.post(url, json={"Username": username, "Pw": password}, headers=header).json()
        ID = response["User"]["Id"]
        print(ID)
        print(f'api_key={response["AccessToken"]}')
        config["account"][key] = ID
        with open("config.ini", "w") as configfile:
            config.write(configfile)
        exit()


def progress_bar(speed_dict):
    slow_flag = 0
    message = "" * 50
    while True:
        if speed_dict["total_length"] < 0 or speed_dict["current_length"] < 0:
            time.sleep(1)
        else:
            speed = (sum(speed_dict["size"]) / (time.time() - min(speed_dict["time"]))) if max(speed_dict["time"]) > min(speed_dict["time"]) else 0
            speed_dict["max_speed"] = max(speed_dict["max_speed"], speed)
            if speed <= speed_dict["max_speed"] / 2 and speed < 1024 * 1024:  # 包括开始时速度为0的情况, 速度<1M/s才考虑重置
                slow_flag += 1
            else:
                slow_flag = 0
            if slow_flag >= 30:  # 超过30秒速度<200kB/s
                speed_dict["stop"] = True
                break
            done = int(50 * speed_dict["current_length"] / speed_dict["total_length"])  # https://stackoverflow.com/a/21868231/5340217
            est = int((speed_dict["total_length"] - speed_dict["current_length"]) / speed) if speed else -1
            print(f'\r{" " * (sum(2 if "\u4e00" <= char <= "\u9fff" else 1 for char in message))}', end="")
            message = f'[{"=" * done}{" " * (50-done)}] {sizeof_fmt(speed)}/s {sizeof_fmt(speed_dict["current_length"])}/{sizeof_fmt(speed_dict["total_length"])} 剩余: {str(datetime.timedelta(seconds=est)) if est>=0 else "未知"}'
            print(f"\r{message}", end="")
            time.sleep(1)


def progress_bar_2(speed_dict):
    from alive_progress import alive_bar

    slow_flag = 0
    with alive_bar(total=speed_dict["total_length"], manual=True, monitor="{count}/{total} [{percent:.0%}]") as bar:
        while True:
            if speed_dict["total_length"] < 0 or speed_dict["current_length"] < 0:
                bar(0)
                time.sleep(1)
            else:
                speed = (sum(speed_dict["size"]) / (time.time() - min(speed_dict["time"]))) if max(speed_dict["time"]) > min(speed_dict["time"]) else 0
                speed_dict["max_speed"] = max(speed_dict["max_speed"], speed)
                if speed <= speed_dict["max_speed"] / 2 and speed < 1024 * 1024:  # 包括开始时速度为0的情况, 速度<1M/s才考虑重置
                    slow_flag += 1
                else:
                    slow_flag = 0
                if slow_flag >= 30:  # 超过30秒速度<200kB/s
                    speed_dict["stop"] = True
                    break
                done = int(50 * speed_dict["current_length"] / speed_dict["total_length"])  # https://stackoverflow.com/a/21868231/5340217
                est = int((speed_dict["total_length"] - speed_dict["current_length"]) / speed) if speed else -1
                # print(f'\r[{"=" * done}{" " * (50-done)}] {sizeof_fmt(speed)}/s {sizeof_fmt(speed_dict["current_length"])}/{sizeof_fmt(speed_dict["total_length"])} 预计: {str(datetime.timedelta(seconds=est))}', end='')
                time.sleep(1)
                bar(speed_dict["current_length"] / speed_dict["total_length"])


def monitor():
    config.read("config.ini")
    while True:
        with open("emby_links.txt", "r") as f:
            urls = [url.strip() for url in f.readlines() if url.strip()]
        if len(urls):
            url = urls[0]
            main(url)  # 包含了\n
            with open("emby_links.txt", "r") as f:
                urls = [url.strip() for url in f.readlines() if url.strip()]  # 有可能在下载过程中更新了文件
            if urls[0] == url:
                urls = [i + "\n" for i in urls if i != url]
                with open("emby_links.txt", "w") as f:
                    f.writelines(urls)
            else:
                print("url changed, old: ", url, "new: ", urls[0])
                break

        time.sleep(1)


def add_list(url: str):
    print(url.strip())
    with open(Path(__file__).parent / "emby_links.txt", "a") as f:
        f.write(url.strip() + "\n")


def check_server(para=None):
    if para:
        add_list(para)
    else:
        monitor()


if __name__ == "__main__":
    # pass
    fire.Fire(check_server)
