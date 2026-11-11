# KClipboard

Trình quản lý clipboard cho KDE Plasma 6 trên Wayland, theo kiểu **Win + V** của Windows 11.

- **Lịch sử clipboard**: văn bản (giữ cả định dạng HTML) và hình ảnh; ghim, xóa, xóa tất cả (giữ mục đã ghim); lưu lại sau khi khởi động lại máy.
- **Dán khi chọn (paste on select)**: bấm vào một mục → popup đóng lại và nội dung được dán ngay vào ứng dụng trước đó. Tự dùng `Ctrl+Shift+V` khi cửa sổ đích là terminal (Konsole, kitty, Alacritty…).
- **Đi theo chuột (follow mouse)**: popup mở ngay tại con trỏ chuột, tự lật lên trên hoặc dịch sang khi gần mép màn hình.
- **Tab Emoji** (hơn 1.800 emoji, tìm kiếm cả tiếng Anh lẫn tiếng Việt, chọn màu da, mục dùng gần đây), **Kaomoji** và **Ký hiệu**. Khi chèn emoji, nội dung clipboard cũ của bạn được khôi phục ngay sau đó, giống Win11.
- **Trang Cài đặt** ngay trong popup (nút ⚙ ở góc phải): thay đổi có hiệu lực ngay, không cần khởi động lại.
- Tự đổi giao diện sáng/tối theo Plasma, dùng màu nhấn của hệ thống.

## Cài đặt

```bash
./install.sh
```

Trình cài đặt chép app vào `~/.local/lib/kclipboard`, tạo lệnh `~/.local/bin/kclipboard`, mục trong menu, tự chạy khi đăng nhập, và gán **Meta+V**. Nếu một ứng dụng khác đang giữ Meta+V (ví dụ CopyQ), nó sẽ sao lưu `~/.config/kglobalshortcutsrc` rồi gỡ phím đó khỏi ứng dụng kia. Không muốn vậy thì chạy `./install.sh --keep-shortcut-owner`.

Gỡ bỏ: `./uninstall.sh` (thêm `--purge` để xóa luôn lịch sử và cấu hình).

Yêu cầu: `python3-pyqt6`, `ydotool` (đang chạy dịch vụ `ydotoold`, user thuộc nhóm `input`), `fonts-noto-color-emoji`, `gdbus`.

## Sử dụng

| Phím | Tác dụng |
|---|---|
| `Meta+V` | Mở/đóng popup tại vị trí chuột |
| `↑` `↓` `Home` `End` | Chọn mục |
| `Enter` / bấm chuột | Dán (`Shift+Enter`: dán dạng văn bản thuần) |
| `Delete` | Xóa mục |
| `Ctrl+P` | Ghim / bỏ ghim |
| `Ctrl+Tab` | Chuyển tab |
| `Esc` / bấm ra ngoài | Đóng |

Trong tab Emoji, gõ để tìm, `↓` để vào lưới, `Enter` để chèn kết quả đầu tiên.

Thứ tự tab: Clipboard, Emoji, Kaomoji, Ký hiệu.

Dòng lệnh: `kclipboard --toggle | --show | --emoji | --settings | --hide | --quit`.
Bấm vào biểu tượng ở khay hệ thống để mở popup; menu chuột phải có *Cài đặt* và *Thoát*.

## Cài đặt trong ứng dụng

Bấm nút ⚙ trong popup (hoặc chạy `kclipboard --settings`):

| Nhóm | Tuỳ chọn |
|---|---|
| Chung | Dán khi chọn · Vị trí mở (con trỏ chuột / giữa màn hình) · Tab hiện khi mở (Clipboard / Emoji / tab dùng lần cuối) · Chạy khi đăng nhập · Ngôn ngữ · Giao diện (theo hệ thống / sáng / tối) |
| Lịch sử | Số mục lưu tối đa · Lưu hình ảnh · Giữ lịch sử sau khi khởi động lại · Xóa lịch sử |
| Dán | Ctrl+Shift+V trong terminal · Khôi phục clipboard sau khi chèn emoji · Độ trễ khi dán |
| Phím tắt | Phím mở KClipboard · Phím mở thẳng tab emoji |

Khi đổi phím tắt, nếu tổ hợp đó đang thuộc về ứng dụng khác thì KClipboard báo tên ứng dụng và giữ nguyên phím cũ.

Mọi thứ được lưu trong `~/.config/kclipboard/config.json`. Hai mục chỉ sửa được bằng tay trong file này (khởi động lại app sau khi sửa): `terminal_classes` (danh sách cửa sổ được coi là terminal) và `restore_delay_ms` (thời gian chờ trước khi trả lại clipboard cũ sau khi chèn emoji, mặc định 900).

## Cách hoạt động

Trên Wayland, ứng dụng không được tự đọc clipboard khi không có focus, không tự đặt được vị trí cửa sổ, và không giả lập được phím. Vì thế app gồm mấy phần:

- `clipd.py`: client Wayland nhỏ viết bằng ctypes, dùng giao thức `ext-data-control-v1` của KWin (có dự phòng `wlr-data-control`) để theo dõi và đặt clipboard mà không cần focus. Không cần cài thêm thư viện nào.
- `kwin/`: KWin script được nạp lúc chạy. Nó đặt popup ngay dưới con trỏ (hoặc giữa màn hình), kích hoạt popup, và báo cho app biết cửa sổ nào đang active (để chọn tổ hợp phím dán cho terminal).
- `ydotool`: gửi `Ctrl+V` sau khi popup đóng và focus đã trở về ứng dụng trước.
- KGlobalAccel (DBus): phím tắt toàn cục, đổi được trong trang Cài đặt hoặc *System Settings → Shortcuts → KClipboard*.

Mục chứa `x-kde-passwordManagerHint` (mật khẩu được sao chép từ KeePassXC…) sẽ không được lưu.

Dữ liệu được lưu ở `~/.local/share/kclipboard/` (gồm `history.json` và thư mục `images/`).
