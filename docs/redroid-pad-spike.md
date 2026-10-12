# Android 13 平板路线 spike（2026-10-12，#27）

任务：[#27](https://github.com/amyteq/at_wxclip/issues/27)；启动授权：[owner comment](https://github.com/amyteq/at_wxclip/issues/27#issuecomment-6117371852)；关联决策：[#26 的 D1](https://github.com/amyteq/at_wxclip/issues/26)。开始于 2026-10-12 14:48 AEDT，一天有效工作量上限。工作分支 `dev/redroid-pad-spike`；独立 worktree 位于原 checkout 的 `.claude/worktrees/redroid-pad-spike`，保留原 checkout 的未提交工作。

## 当前结论

**当前实测组合不能作为 gateway 客户端，停在步骤①的登录前启动验证。** redroid 能启动 Android 13、ABI 包含 ARM64，但官网当前 WeChat 8.0.79 在不同设备属性及有/无代理时均崩溃；官网兼容包 8.0.42 也不能正常启动。官方 Android 13 Google APIs x86_64 AVD revision 17 能启动，却没有 ARM 翻译，安装当前 APK 明确失败。两个环境的 Android 默认 CA 验证都通过，但 WeChat 自身的 CA 信任、平板身份与视频号路径尚未验证。D1：本次组合不可用；更广泛的 pad 路线仍未证实，不能据此断言「视频号必须 hook」，也不能声称已经验证容器化 Linux gateway。

## 1. 宿主检查

以下命令在宿主环境执行。受限执行环境曾看不到 `/dev/kvm`，重新在宿主设备视图下检查后确认存在，不能把沙箱的设备视图当成宿主能力。

```bash
uname -r
rg binder /proc/filesystems
zcat /proc/config.gz | rg 'ANDROID_BINDER|ASHMEM'
docker info --format 'Server={{.ServerVersion}} CgroupVersion={{.CgroupVersion}} Driver={{.Driver}}'
ls -l /dev/kvm
/opt/android-sdk/emulator/emulator -accel-check
df -h /home/harry/test
```

| 检查 | 实测 |
|---|---|
| 内核 | `7.2.4-arch1-2` |
| binder 文件系统 | `nodev binder` |
| 内核配置 | `CONFIG_ANDROID_BINDER_IPC_RUST=y`；`CONFIG_ANDROID_BINDER_IPC` 未启用；`CONFIG_ANDROID_BINDER_DEVICES=""` |
| Docker | `29.7.2`；cgroup `2`；storage driver `overlay2` |
| KVM | `/dev/kvm` 存在；`KVM (version 12) is installed and usable.` |
| 工作盘剩余空间 | 233 GiB |

没有安装内核、加载模块、重启宿主、使用 sudo、调整宿主网络或改动 M0 的 Flatpak 设置。Docker 使用现有默认网桥，网关经 `docker network inspect bridge --format '{{range .IPAM.Config}}{{.Gateway}}{{end}}'` 查得为 `172.17.0.1`。

## 2. redroid 启动与 ARM 翻译

从独立 worktree 执行；Android 数据和 CA 在仓库外，APK、日志和临时探针在 Git 忽略的 `temp/` 中。路径是本次实测的绝对路径；其他机器应替换成本人的专用目录并重新查询网桥地址。

```bash
mkdir -p temp
mkdir -p /home/harry/.local/share/wx-dlh/redroid-27/data /home/harry/.local/share/wx-dlh/redroid-27/ca
chmod 700 /home/harry/.local/share/wx-dlh/redroid-27
docker pull redroid/redroid:13.0.0-latest
docker run -d --name redroid-wx-dlh-27 --privileged \
  -v /home/harry/.local/share/wx-dlh/redroid-27/data:/data \
  -p 127.0.0.1:5555:5555 \
  redroid/redroid:13.0.0-latest \
  androidboot.use_memfd=true \
  androidboot.redroid_width=1600 \
  androidboot.redroid_height=2560 \
  androidboot.redroid_dpi=320 \
  androidboot.redroid_net_proxy_type=static \
  androidboot.redroid_net_proxy_host=172.17.0.1 \
  androidboot.redroid_net_proxy_port=8899
docker image inspect redroid/redroid:13.0.0-latest --format '{{.Id}} {{join .RepoDigests ","}}'
adb connect 127.0.0.1:5555
adb -s 127.0.0.1:5555 shell getprop sys.boot_completed
adb -s 127.0.0.1:5555 shell getprop ro.build.version.release
adb -s 127.0.0.1:5555 shell getprop ro.product.cpu.abilist
adb -s 127.0.0.1:5555 shell getprop ro.dalvik.vm.native.bridge
adb -s 127.0.0.1:5555 shell wm size
adb -s 127.0.0.1:5555 shell wm density
```

| 项目 | 实测 |
|---|---|
| 镜像 tag | `redroid/redroid:13.0.0-latest` |
| manifest digest | `sha256:41e5f0c1ff27a4a474c474e5595168cedf6c40fc5dd102c5617f48c80f511e9e` |
| 本地 amd64 image ID | `sha256:439b3b1f65db3b66d699e1ffeb8d257012c1722c213dcb4f90db77173cf0b7a4` |
| 完成启动 | `1` |
| Android | `13` |
| ABI | `x86_64,arm64-v8a,x86,armeabi-v7a,armeabi` |
| native bridge | `libnb.so` |
| 显示 | `1600x2560` / `320` dpi；最小宽度 800 dp |
| 初始设备属性 | model `redroid13_x86_64`、brand `redroid`、characteristics `default` |

`ss -ltn` 确认 adb 容器端口为 `127.0.0.1:5555`，adb server 为 `127.0.0.1:5037`。启动没有失败；后备 AVD 的触发原因是 WeChat 的 ARM64 代码在当前 redroid 翻译层崩溃。

## 3. 代理与系统 CA

```bash
make setup
.venv/bin/python -m wx_dlh ca --ca-dir /home/harry/.local/share/wx-dlh/redroid-27/ca > temp/ca-setup.txt
openssl x509 -in /home/harry/.local/share/wx-dlh/redroid-27/ca/mitmproxy-ca-cert.pem -subject_hash_old -noout
```

本次 subject hash 为 `c8750f0d`。在独立终端持续运行代理：

```bash
.venv/bin/python -m wx_dlh proxy --host 172.17.0.1 --port 8899 \
  --ca-dir /home/harry/.local/share/wx-dlh/redroid-27/ca \
  --capture-dir temp/redroid-captures > temp/proxy.log 2>&1
```

最初用短命执行会话的 `nohup ... &` 启动，进程随会话退出而结束；改成长驻执行会话后监听成功。`ss -ltn` 确认只监听网桥 `172.17.0.1:8899`。没有调用宿主 CA trust 脚本。

```bash
adb -s 127.0.0.1:5555 root
adb -s 127.0.0.1:5555 shell settings put global http_proxy 172.17.0.1:8899
adb -s 127.0.0.1:5555 shell settings get global http_proxy
adb -s 127.0.0.1:5555 shell 'test -w /system/etc/security/cacerts && echo system-ca-directory-writable'
adb -s 127.0.0.1:5555 push /home/harry/.local/share/wx-dlh/redroid-27/ca/mitmproxy-ca-cert.pem /system/etc/security/cacerts/c8750f0d.0
adb -s 127.0.0.1:5555 shell 'chown 0:0 /system/etc/security/cacerts/c8750f0d.0 && chmod 644 /system/etc/security/cacerts/c8750f0d.0 && restorecon /system/etc/security/cacerts/c8750f0d.0'
```

redroid 的系统 CA 目录已可写，不需要 remount。只复制公钥证书 `mitmproxy-ca-cert.pem`，私钥没有进入 Android。启动参数没有让 `settings get global http_proxy` 返回值（最初为 `null`），因此显式写入后回查为 `172.17.0.1:8899`。

宿主 curl 经代理使用该 CA 访问官网返回 200；随后用 Android 自身 `AndroidCAStore` 和默认 `HttpsURLConnection` 单独验证，未替换 TrustManager、未关闭主机名验证。临时 Java 探针如下，保存为 `temp/AndroidTlsProbe.java`：

```java
import java.net.InetSocketAddress;
import java.net.Proxy;
import java.net.URL;
import java.security.KeyStore;
import java.security.cert.Certificate;
import java.security.cert.X509Certificate;
import java.util.Enumeration;
import javax.net.ssl.HttpsURLConnection;
public class AndroidTlsProbe {
  public static void main(String[] args) throws Exception {
    KeyStore store = KeyStore.getInstance("AndroidCAStore");
    store.load(null);
    int count = 0;
    for (Enumeration<String> e=store.aliases(); e.hasMoreElements();) {
      String alias=e.nextElement();
      Certificate cert=store.getCertificate(alias);
      if (cert instanceof X509Certificate && ((X509Certificate) cert).getSubjectX500Principal().getName().contains("mitmproxy")) count++;
    }
    System.out.println("AndroidCAStore mitmproxy certificate count="+count);
    Proxy proxy=new Proxy(Proxy.Type.HTTP,new InetSocketAddress("172.17.0.1",8899));
    HttpsURLConnection c=(HttpsURLConnection)new URL("https://weixin.qq.com/").openConnection(proxy);
    c.setConnectTimeout(15000); c.setReadTimeout(15000);
    System.out.println("HTTPS status="+c.getResponseCode());
    System.out.println("TLS cipher="+c.getCipherSuite());
    System.out.println("Issuer="+((X509Certificate)c.getServerCertificates()[0]).getIssuerX500Principal().getName());
    c.disconnect();
  }
}
```

```bash
mkdir -p temp/android-tls-probe
javac -source 8 -target 8 -d temp/android-tls-probe temp/AndroidTlsProbe.java
/opt/android-sdk/build-tools/36.1.0/d8 --output temp/android-tls-probe temp/android-tls-probe/AndroidTlsProbe.class
adb -s 127.0.0.1:5555 push temp/android-tls-probe/classes.dex /data/local/tmp/wx-dlh-tls-probe.dex
adb -s 127.0.0.1:5555 shell 'CLASSPATH=/data/local/tmp/wx-dlh-tls-probe.dex app_process /system/bin AndroidTlsProbe'
```

实测输出：`AndroidCAStore mitmproxy certificate count=1`、`HTTPS status=200`、`TLS cipher=TLS_AES_256_GCM_SHA384`、`Issuer=O=mitmproxy,CN=mitmproxy`。这是 **Android 默认信任库成功**，不等于 WeChat 已接受 CA。预检日志中的 Google 后台 TLS 拒绝与官网探针要分开计数，不能报告「全部客户端无 TLS 错误」。

## 4. 官网 APK 与启动验证

官网页面 `https://weixin.qq.com/` 于本次执行直接抓取，当前主下载链接如下。没有使用第三方 APK、补丁、Frida、Xposed 或机型判断 hook。

```bash
curl -fL --max-time 30 https://weixin.qq.com/ -o temp/weixin-official.html
rg -o 'https?[^" <>]+\.apk[^" <>]*' temp/weixin-official.html
curl -fL --retry 2 --max-time 180 https://dldir1v6.qq.com/weixin/android/weixin8079android3200_0x28004f30_arm64_1.apk -o temp/weixin8079android3200_0x28004f30_arm64_1.apk
sha256sum temp/weixin8079android3200_0x28004f30_arm64_1.apk
/opt/android-sdk/build-tools/36.1.0/aapt dump badging temp/weixin8079android3200_0x28004f30_arm64_1.apk
/opt/android-sdk/build-tools/36.1.0/apksigner verify --print-certs temp/weixin8079android3200_0x28004f30_arm64_1.apk
adb -s 127.0.0.1:5555 install temp/weixin8079android3200_0x28004f30_arm64_1.apk
adb -s 127.0.0.1:5555 shell am start -W -n com.tencent.mm/.ui.LauncherUI
adb -s 127.0.0.1:5555 shell pidof com.tencent.mm
adb -s 127.0.0.1:5555 logcat -b crash -d -t 140 > temp/wechat-8079-crash.txt
```

- APK：`com.tencent.mm`，`8.0.79`，versionCode `3200`，minSdk `24`，targetSdk `34`，仅 `arm64-v8a`。
- APK SHA-256：`f2a725bc04302ad218a563533ae79356e66b64aa711b275bc919a8d432f7e15d`。
- 签名校验成功；签名者 Tencent；证书 SHA-256：`0fe4ff85c215918396dadc7cd8ce6963339af33d37751a56e54c7206b63a3c7c`。
- 安装返回 `Success`；启动命令返回 `Status: ok` / `WeChatSplashActivity`，但随后进程退出，因此不能把 `am start` 的成功当作应用可用。
- 03:53:32 UTC 崩溃：`SIGABRT`，`Abort message: 'Cannot process signal 11'`；栈包括 `libndk_translation.so` 的 `HandleHostSignal`、`DoTrampoline_JNIEnv_GetStaticMethodID` 以及应用的 `libowl.so`。

排除代理影响的第二次启动：

```bash
adb -s 127.0.0.1:5555 shell settings put global http_proxy :0
adb -s 127.0.0.1:5555 shell am force-stop com.tencent.mm
adb -s 127.0.0.1:5555 shell am start -W -n com.tencent.mm/.ui.LauncherUI
adb -s 127.0.0.1:5555 shell pidof com.tencent.mm
adb -s 127.0.0.1:5555 logcat -b crash -d -t 30
```

03:54:57 UTC 再次同样崩溃。没有扫码或输入凭据，手机和平板身份验证尚未开始；没有尝试修改 WeChat 或翻译库。原因只能定位为此组合的 native translation 启动失败，尚未证明具体根因。

### 设备属性补测

在停止初始容器后，用同一专用 `/data` 启动了新的容器。最初尝试 `ro.product.brand=Lenovo ro.product.manufacturer=LENOVO 'ro.product.model=Lenovo TB-9707F' ro.build.characteristics=tablet`，但 `getprop ro.product.model` 实际只返回 `Lenovo`；redroid 对带空格的参数发生截断，因此这不算成功测试了完整的 Lenovo 机型。该组参数仍然出现相同 native translation 崩溃。

第一组属性的完整命令（初始容器先停止，以释放同一个 adb 端口）：

```bash
docker stop redroid-wx-dlh-27
docker run -d --name redroid-wx-dlh-27-tablet --privileged \
  -v /home/harry/.local/share/wx-dlh/redroid-27/data:/data \
  -p 127.0.0.1:5555:5555 redroid/redroid:13.0.0-latest \
  androidboot.use_memfd=true androidboot.redroid_width=1600 \
  androidboot.redroid_height=2560 androidboot.redroid_dpi=320 \
  ro.product.brand=Lenovo ro.product.manufacturer=LENOVO \
  'ro.product.model=Lenovo TB-9707F' ro.build.characteristics=tablet
```

随后改用不含空格的 tablet 型号参数，实际命令为：

```bash
docker stop redroid-wx-dlh-27-tablet
docker rm redroid-wx-dlh-27-tablet
docker run -d --name redroid-wx-dlh-27-tablet --privileged \
  -v /home/harry/.local/share/wx-dlh/redroid-27/data:/data \
  -p 127.0.0.1:5555:5555 redroid/redroid:13.0.0-latest \
  androidboot.use_memfd=true androidboot.redroid_width=1600 \
  androidboot.redroid_height=2560 androidboot.redroid_dpi=320 \
  ro.product.brand=samsung ro.product.manufacturer=samsung \
  ro.product.model=SM-X610 ro.build.characteristics=tablet
adb connect 127.0.0.1:5555
adb -s 127.0.0.1:5555 shell getprop ro.product.model
adb -s 127.0.0.1:5555 shell getprop ro.build.characteristics
```

回查为 `SM-X610` / `tablet`。这些属性只能作为待验证的输入，不能代替 owner 手机上的设备分类或电脑端共存证据。

SM-X610 组也在 04:01:22 UTC 出现相同 `Cannot process signal 11` / `libowl.so` 崩溃。三组设备属性都未进入登录；不能把它们标成「微信未认成平板」，只能记为「登录前崩溃，身份未验证」。

## 5. 官方 Android 13 AVD 后备实测

redroid 容器本身没有启动失败，后备原因是 WeChat 在当前翻译层不能运行。已有 emulator 为 `36.3.10.0`（build `14472402`）；没有改动 `/opt/android-sdk` 的安装。先尝试把镜像下载到专用 SDK 根目录：

```bash
mkdir -p /home/harry/.local/share/wx-dlh/redroid-27/android-sdk/licenses
cp /opt/android-sdk/licenses/* /home/harry/.local/share/wx-dlh/redroid-27/android-sdk/licenses/
/opt/android-sdk/cmdline-tools/latest/bin/sdkmanager --sdk_root=/home/harry/.local/share/wx-dlh/redroid-27/android-sdk 'system-images;android-33;google_apis;x86_64' > temp/avd-install.log 2>&1
```

该过程持续无输出，于约三分钟时结束本次 SDK manager 进程，改为从 Google 官方索引获取同一包。已有 licenses 是本机此前接受的 SDK licenses；没有代替 owner 接受新的条款。

```bash
curl -fsSL --connect-timeout 10 --max-time 30 https://dl.google.com/android/repository/sys-img/google_apis/sys-img2-3.xml -o temp/google-apis-images.xml
curl -fL --retry 2 --connect-timeout 10 --max-time 600 https://dl.google.com/android/repository/sys-img/google_apis/x86_64-33_r17.zip -o temp/x86_64-33_r17.zip
sha1sum temp/x86_64-33_r17.zip
unzip -q temp/x86_64-33_r17.zip -d /home/harry/.local/share/wx-dlh/redroid-27/android-sdk/system-images/android-33/google_apis
cat /home/harry/.local/share/wx-dlh/redroid-27/android-sdk/system-images/android-33/google_apis/x86_64/source.properties
```

索引指定 `system-images;android-33;google_apis;x86_64`、revision `17`、大小 `1707857511` 字节。下载 SHA-1 为 `2b96f5bd5c79bfe1cc645e70b3e630b5755d9711`，与索引一致；归档 SHA-256 为 `0455f853dcaf23e89b44f810e4b6391e8ef356326ec0e6c25b7f3f9de9ff5659`。`source.properties` 回查 API `33` / ABI `x86_64` / revision `17`。

AVD manager 初次仅设 `ANDROID_HOME` / `ANDROID_SDK_ROOT` 时仍从 `/opt/android-sdk` 找镜像，报 `Package path is not valid`（只列出本机已有 Android 35 Play 镜像）。以下 JVM toolsdir 覆盖后成功创建专用 Pixel Tablet AVD：

```bash
mkdir -p /home/harry/.local/share/wx-dlh/redroid-27/avd
mkdir -p /home/harry/.local/share/wx-dlh/redroid-27/android-sdk/cmdline-tools/latest
ANDROID_AVD_HOME=/home/harry/.local/share/wx-dlh/redroid-27/avd \
AVDMANAGER_OPTS=-Dcom.android.sdkmanager.toolsdir=/home/harry/.local/share/wx-dlh/redroid-27/android-sdk/cmdline-tools/latest \
/opt/android-sdk/cmdline-tools/latest/bin/avdmanager create avd \
  -n wx-dlh-pad-27 -k 'system-images;android-33;google_apis;x86_64' \
  -d pixel_tablet -p /home/harry/.local/share/wx-dlh/redroid-27/avd/wx-dlh-pad-27.avd
ANDROID_HOME=/home/harry/.local/share/wx-dlh/redroid-27/android-sdk \
ANDROID_AVD_HOME=/home/harry/.local/share/wx-dlh/redroid-27/avd \
/opt/android-sdk/emulator/emulator -avd wx-dlh-pad-27 \
  -sysdir /home/harry/.local/share/wx-dlh/redroid-27/android-sdk/system-images/android-33/google_apis/x86_64 \
  -writable-system -no-snapshot -no-window -no-audio -gpu swiftshader \
  -memory 3072 -cores 2 -port 5556 > temp/emulator.log 2>&1
```

回查 `sys.boot_completed=1`、Android `13`、`2560x1600 / 320dpi`，硬件配置为 `pixel_tablet`，PlayStore 未启用。注意硬件 profile 不会自动把 Android 的 model 改成真实平板：model 实测 `sdk_gphone64_x86_64`，characteristics 为 `emulator`。构建 fingerprint 为 `google/sdk_gphone64_x86_64/emu64x:13/TE1A.240213.009/12342917:userdebug/dev-keys`。控制与 adb 端口 `5556/5557` 只监听 `127.0.0.1` / `::1`。

```bash
adb -s emulator-5556 shell getprop ro.product.cpu.abilist
adb -s emulator-5556 shell getprop ro.dalvik.vm.native.bridge
adb -s emulator-5556 shell 'ls /system/lib64/*translation* /system/lib64/libnb* /system/bin/arm64 /system/lib64/arm64 2>/dev/null'
adb -s emulator-5556 install temp/weixin8079android3200_0x28004f30_arm64_1.apk
```

ABI 只有 `x86_64`；native bridge 为 `0`，没有找到 ARM translation 库/目录（名字匹配 `libnb*` 的 `libnbaio.so` / `libnblog.so` 不是 native bridge）。安装返回：

```text
INSTALL_FAILED_NO_MATCHING_ABIS: Failed to extract native libraries, res=-113
```

因此「Android 11+ 的 x86_64 Google APIs 镜像一定自带可用 ARM64 翻译」不能作为本 spike 的前提；**本次 Android 13 revision 17 实测不满足**。没有移植其他镜像的 translator，没有修改 APK。

### AVD 的代理/系统 CA 独立验证

在单独终端运行现有代理，日志与 redroid 分开：

```bash
.venv/bin/python -m wx_dlh proxy --host 127.0.0.1 --port 8899 \
  --ca-dir /home/harry/.local/share/wx-dlh/redroid-27/ca \
  --capture-dir temp/avd-captures > temp/avd-proxy.log 2>&1
adb -s emulator-5556 root
adb -s emulator-5556 remount
adb -s emulator-5556 reboot
adb -s emulator-5556 wait-for-device
adb -s emulator-5556 root
adb -s emulator-5556 remount
adb -s emulator-5556 push /home/harry/.local/share/wx-dlh/redroid-27/ca/mitmproxy-ca-cert.pem /system/etc/security/cacerts/c8750f0d.0
adb -s emulator-5556 shell 'chown 0:0 /system/etc/security/cacerts/c8750f0d.0 && chmod 644 /system/etc/security/cacerts/c8750f0d.0 && restorecon /system/etc/security/cacerts/c8750f0d.0'
adb -s emulator-5556 shell settings put global http_proxy 10.0.2.2:8899
sed 's/172.17.0.1/10.0.2.2/' temp/AndroidTlsProbe.java > temp/android-tls-probe/AndroidTlsProbe.java
javac -source 8 -target 8 -d temp/android-tls-probe temp/android-tls-probe/AndroidTlsProbe.java
/opt/android-sdk/build-tools/36.1.0/d8 --output temp/android-tls-probe temp/android-tls-probe/AndroidTlsProbe.class
adb -s emulator-5556 push temp/android-tls-probe/classes.dex /data/local/tmp/wx-dlh-tls-probe.dex
adb -s emulator-5556 shell 'CLASSPATH=/data/local/tmp/wx-dlh-tls-probe.dex app_process /system/bin AndroidTlsProbe'
```

第一次 remount 提示为 system 禁用 verity 并启用 overlayfs，要求重启；重启后 root/remount 成功。以上修改仅在专用 guest 内。Android TLS 探针同样返回 1 张 CA、HTTPS 200、`TLS_AES_256_GCM_SHA384`、issuer `mitmproxy`。CA 的 guest 写入不等于可跨新启动复用；后续重新启动必须再次回查证书和探针结果。

## 6. 官网兼容 APK 的有界补测

官网同页还链接了 `https://dldir1v6.qq.com/weixin/android/weixin8042android2460.apk`。为区分当前 ARM64 构建与老兼容构建，另用全新 `/data` 做一次补测，没有把它替换到原环境。

```bash
curl -fL --retry 2 --max-time 180 https://dldir1v6.qq.com/weixin/android/weixin8042android2460.apk -o temp/weixin8042android2460.apk
sha256sum temp/weixin8042android2460.apk
/opt/android-sdk/build-tools/36.1.0/aapt dump badging temp/weixin8042android2460.apk
/opt/android-sdk/build-tools/36.1.0/apksigner verify --print-certs temp/weixin8042android2460.apk
docker stop redroid-wx-dlh-27-tablet
mkdir -p /home/harry/.local/share/wx-dlh/redroid-27/data-8042
docker run -d --name redroid-wx-dlh-27-compat --privileged \
  -v /home/harry/.local/share/wx-dlh/redroid-27/data-8042:/data \
  -p 127.0.0.1:5555:5555 redroid/redroid:13.0.0-latest \
  androidboot.use_memfd=true androidboot.redroid_width=1600 \
  androidboot.redroid_height=2560 androidboot.redroid_dpi=320 \
  ro.product.brand=samsung ro.product.manufacturer=samsung \
  ro.product.model=SM-X610 ro.build.characteristics=tablet
adb connect 127.0.0.1:5555
adb -s 127.0.0.1:5555 install temp/weixin8042android2460.apk
adb -s 127.0.0.1:5555 shell am start -W -n com.tencent.mm/.ui.LauncherUI
```

安装前也按第 3 节将公钥 CA 放进该容器并把系统代理设为 `172.17.0.1:8899`。APK 为 `8.0.42` / versionCode `2460`，只带 `armeabi-v7a`；SHA-256 为 `c5e01d8fd9786d153ec4bfaef6ab28d1a754e299506bf74033c790b694333d18`，签名证书与 8.0.79 相同且验签通过。安装成功，但主进程和 `:cuploader` 反复退出并重启，不能因为 `pidof` 短暂有输出就当作稳定运行。错误为：

```text
dlopen failed: .../app_recovery_lib/libstlport_shared.so is for EM_ARM (40) instead of EM_386 (3)
```

已执行 `adb -s 127.0.0.1:5555 shell am force-stop com.tencent.mm` 停止重试。没有进入扫码，不再扩大版本矩阵；该补测只说明这一个官方兼容包也未解决启动问题。

## 7. 步骤①②③与 AC 状态

| 参数 / 路线 | 启动实测 | 平板身份、电脑共存 |
|---|---|---|
| redroid 默认 model + 1600×2560 / 320dpi，8.0.79 | 有代理、无代理都在 native translation 中崩溃 | 未验证，未登录 |
| redroid Lenovo 参数 + tablet，8.0.79 | model 被截断为 `Lenovo`；同样崩溃 | 未验证，不能称为完整 Lenovo 型号测试 |
| redroid SM-X610 + tablet，8.0.79 | 参数回查正确；同样崩溃 | 未验证，未登录 |
| AVD Pixel Tablet profile / Android 13 r17，8.0.79 | OS 启动，APK 安装因缺少匹配 ABI 失败 | 未验证，未登录 |
| redroid SM-X610 + tablet，全新 data，8.0.42 | ARM32 库加载错误，反复退出 | 未验证，未登录 |

- **①：停在登录前的应用启动。** 这不是扫码不可用，也不是 owner 不在线造成的等待；目前没有可交给 owner 扫码的稳定客户端。
- **②：未运行。** 未确认视频号入口，没有播放视频。来自 Android/Google 后台与 TLS 探针的 host 数量不能用作 AC4 的证据；没有可报告的 WeChat Channels 播放会话。
- **③：未运行。** 没有检查到属于视频播放的 proxy-visible response，不能把「没有播放」记成「响应里没有 decode_key」。未创建 hook 决策 issue。
- **adb 自动化：未运行。** 路线没有满足前置条件，没有新增 `tools/` 自动化脚本。

| AC | 状态 | 证据 / 缺口 |
|---|---|---|
| AC1 | Android 基础启动通过；应用兼容性失败 | redroid Android 13、ARM64 ABI 和本地 adb 实测；AVD 独立启动但缺少 ARM ABI |
| AC2 | 部分完成 | 两条环境的安装、镜像、APK、代理、系统 CA 可复现；WeChat 自身接受 CA 尚未验证 |
| AC3 | 未运行，受应用启动阻塞 | 全部参数如上；无 owner 登录及设备槽观察 |
| AC4 | 未运行，依赖 AC3 | 没有 Channels 播放，不能给出 H5/bridge 的阴性结论 |
| AC5 | 未运行，依赖 AC4 | 没有进入 key 来源验证，不满足另开 hook issue 的触发条件 |
| AC6 | 未运行 | 停在步骤①；没有自动化播放 |
| AC7 | 文档与阶段报告完成；完整路线结论受阻 | 本文、project background、development plan，以及 #27 / #26 的报告；D1 为当前组合不可用，pad 普遍可行性未决 |
| AC8 | 检查通过 | `make lint test`：ruff 通过，434 tests passed（21.51s）；仅文档提交，运行产物留在忽略/外部目录 |

## 8. 清理、有效工作量与停止点

本轮有效工作约 25 分钟，未耗尽一天上限；停止原因是所测客户端组合均无法进入登录。已执行 `adb -s emulator-5556 emu kill`、`docker stop redroid-wx-dlh-27-compat`，初始 redroid 与 tablet 容器此前已停止；两个本次代理进程也已结束。复查 `5555/5556/5557/8899` 没有本次监听。Docker stop 最后以 137 退出，属于主动停止本次容器，不应当作 Android 启动失败证据。专用 `data/`、`data-8042/`、AVD、SDK、CA 均保留，没有删除 owner 数据。

本 PR 只提交三份文档；APK、原始崩溃日志、流量、探针和依赖保留在 `temp/` 或仓库外，没有提交 APK、Android data、媒体、key 值、CA 材料或 WeChat 账号。

### 下一步

本次不需要 owner 扫码：客户端启动问题先于人工验证。若要继续 pad 路线，下一步应先提供或验证一个能稳定运行原版 WeChat 的 Android 13 + ARM64 环境（例如可用的 ARM64 测试主机），或单独确定 native translation 兼容性修复范围。不能用换内核来解释/解决已经成功启动的 binder，也不能因启动失败跳到「需要视频号 hook」的结论。容器化 Linux 客户端可作为 #26 的后续候选，但其 gateway 组合未在本 spike 实测，不能据 M0 自动宣布它通过。

后续一旦客户端能稳定启动，再交 owner 扫码，并由 owner 在 #27 回报：实际 WeChat 版本和设备参数组、手机登录设备列表是否显示 Android 平板、原电脑客户端是否仍在线；不要贴账号或二维码。只有步骤①通过，才继续一次视频播放及 AC4/AC5；不做无人值守或批量播放。

已在执行过程中多次复查 #27 新评论；本会话没有可调用的 `automation_update` 或等价任务调度工具，**未建立持久定时监控**，也没有创建宿主 cron/service。恢复任务时先读 #27 新评论，再按此处停止点继续；启用定时任务后，应只在有新问题、新验证结果或状态变化时追加报告，无变化时保持安静。

参考：[redroid 参数与 native bridge](https://github.com/remote-android/redroid-doc)、[Android 系统 CA](https://docs.mitmproxy.org/stable/howto/install-system-trusted-ca-android/)、[官方 emulator 参数](https://developer.android.com/studio/run/emulator-commandline)。外部资料用于准备命令；本页结论以实际输出为准。
