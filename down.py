import datetime
import multiprocessing as mp
import re
import time
import urllib.parse as parse
from configparser import ConfigParser
from multiprocessing.connection import Connection
from pathlib import Path

import fire
import requests
from tqdm import tqdm

s = requests.Session()
config = ConfigParser()


def main(record: str):
    if config.has_option("settings", "folder"):
        folder_loc = config["settings"]["folder"]
    else:
        print("no folder location in settings")
        exit()

    filename, url = record.rsplit(" ", 1)
    file_loc = Path(folder_loc) / filename
    start_loc = file_loc.stat().st_size if file_loc.exists() else 0
    start = time.time()
    print(f"下载: {file_loc.name}")
    restart = True  # 是否进入循环
    pipe_recv, pipe_send = mp.Pipe(duplex=False)
    while restart:
        pbar = None
        max_speed = 0  # 0表示无法正常连接
        total_length = -1
        current_length = -2
        slow_flag = 0

        p_download_file = mp.Process(target=emby_download, args=(url, file_loc, pipe_send), daemon=True)
        p_download_file.start()
        while True:
            if pipe_recv.poll():
                if pbar is None:
                    total_length, current_length = pipe_recv.recv()
                    pbar = tqdm(
                        total=total_length,
                        initial=current_length,
                        unit_scale=True,
                        unit_divisor=1024,
                        unit="B",
                        bar_format="|{bar:50}| {rate_fmt} {n_fmt}/{total_fmt} 剩余: {remaining}",
                        leave=False,
                        miniters=0,
                    )
                else:
                    n = pipe_recv.recv()
                    pbar.update(n)
                    current_length += n
            elif pbar is not None:
                pbar.update(0)
            if pbar is None or (rate := pbar.format_dict["rate"]) is None:
                rate = 0
            max_speed = max(max_speed, rate)
            if rate <= max_speed / 2 and rate < 1024 * 1024:
                slow_flag += 1
            else:
                slow_flag = 0
            if slow_flag >= 30:
                if pbar is None:
                    print("速度太慢, 5秒后重新连接")
                else:
                    pbar.display(mask_str("速度太慢, 5秒后重新连接", pos=int(pbar.n / pbar.total * 50)))
                p_download_file.terminate()
                p_download_file.join()
                time.sleep(5)
                break
            elif not p_download_file.is_alive():
                if current_length == total_length and total_length > 0:  # 下载完成
                    end = time.time()
                    elapsed_time = end - start
                    time_str = ""

                    for unit_divisor, unit in [(60, "s"), (60, "m"), (24, "h"), (100000, "d")]:
                        if elapsed_time == 0:
                            break
                        temp, remainder = divmod(elapsed_time, unit_divisor)
                        if unit == "s":
                            remainder = round(remainder, 2)
                            temp = int(temp)
                        elapsed_time = temp
                        time_str = f"{remainder}{unit}{time_str}"

                    pbar.display(mask_str(f"耗时: {time_str}, 平均速度: {tqdm.format_sizeof((total_length-start_loc)/(end - start), suffix='B', divisor=1024)}/s", pos=50))
                    restart = False
                    break
                else:  # 下载报错, p_download_file停止
                    if pbar is None:
                        print("下载失败, 5秒后重新连接")
                    else:
                        pbar.display(mask_str("下载失败, 5秒后重新连接", pos=int(pbar.n / pbar.total * 50)))
                    time.sleep(5)
                    break
            time.sleep(1)
        if pbar is not None:
            pbar.close()
        pbar = None
    pipe_recv.close()


def emby_download(url: str, file_loc: Path, pipe_send: Connection):
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
            total_length_raw = response.headers.get("content-length")
            if total_length_raw is None:  # no content length header
                return response.content

            pipe_send.send((int(response.headers["Content-Range"].split("/")[1]), start_loc))
            # method_iter_conent(file_loc, response, current_length) # 使用iter_content, 无法解决在某时刻没有速度的情况, 且最大速度为1M/s
            method_shutil(file_loc, response, pipe_send)  # 使用 shutil.copyfileobj, 最快能达8M/s
    except Exception as e:
        print(response.headers)
        raise e


def method_shutil(file_loc: Path, response: requests.Response, pipe_send: Connection):
    with open(file_loc, "ab") as f:  # https://stackoverflow.com/a/29967714/5340217
        length = 16 * 1024 * 1024
        with memoryview(bytearray(length)) as mv:
            while True:
                n = response.raw.readinto(mv)
                if not n:
                    print("\nnot n\n")
                    break
                elif n < length:
                    pipe_send.send(n)
                    with mv[:n] as smv:
                        f.write(smv)
                    break
                else:
                    pipe_send.send(n)
                    f.write(mv)


def get_filename(url: str, folder_loc: str):
    # return folder_loc/"stream.mkv"
    o = parse.urlsplit(url)
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
        filename: str = response_json["Name"]
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
        with open(Path(__file__).parent / "config.ini", "w", encoding="utf-8") as configfile:
            config.write(configfile)
        exit()


def mask_str(msg: str, pos: int):
    if pos == 0:
        return "|" + msg + "\n"
    else:
        cur_pos = 0
        for i, c in enumerate(msg):
            cur_pos += 2 if 0x2E80 <= ord(c) <= 0x9FFF else 1
            if cur_pos >= pos:
                break
        return "|\033[30;47m" + msg[: i + 1] + "\033[0m" + msg[i + 1 :] + "\n"


def monitor():
    while True:
        with open(Path(__file__).parent / "emby_links.txt", "r", encoding="utf-8") as f:
            records = [record.strip() for record in f.readlines() if record.strip()]
        if len(records):
            record = records[0]
            main(record)  # 格式为 Vigil.S02E03.mkv url
            with open(Path(__file__).parent / "emby_links.txt", "r", encoding="utf-8") as f:
                records = [url.strip() for url in f.readlines() if url.strip()]  # 有可能在下载过程中更新了文件
            if records[0] == record:
                records = [i + "\n" for i in records if i != record]
                with open(Path(__file__).parent / "emby_links.txt", "w", encoding="utf-8") as f:
                    f.writelines(records)
            else:
                print("url changed, old: ", record, "new: ", records[0])
                break

        time.sleep(1)


def add_list(url: str):
    if config.has_option("settings", "folder"):
        folder_loc = config["settings"]["folder"]
    else:
        print("no folder location in settings")
        exit()

    url = url.strip()
    filename = get_filename(url, folder_loc)  # 顺便下载字幕

    print(filename, url)
    with open(Path(__file__).parent / "emby_links.txt", "a", encoding="utf-8") as f:
        f.write(f"{filename} {url}\n")


def check_server(para=None):
    if para:
        add_list(para)
    else:
        monitor()


if __name__ == "__main__":
    config.read(Path(__file__).parent / "config.ini", encoding="utf-8")
    fire.Fire(check_server)
