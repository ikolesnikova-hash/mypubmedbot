# Отключает режим выделения мышью в окне консоли,
# чтобы случайный щелчок по окну не ставил бота на паузу.
$k = Add-Type -PassThru -Name K -Namespace W -MemberDefinition @'
[DllImport("kernel32.dll")] public static extern IntPtr GetStdHandle(int h);
[DllImport("kernel32.dll")] public static extern bool GetConsoleMode(IntPtr h, out uint m);
[DllImport("kernel32.dll")] public static extern bool SetConsoleMode(IntPtr h, uint m);
'@
$h = $k::GetStdHandle(-10)   # ввод консоли
$mode = [uint32]0
if ($k::GetConsoleMode($h, [ref]$mode)) {
    # 0x80 = ENABLE_EXTENDED_FLAGS (нужен для изменения), 0x40 = QUICK_EDIT (выключаем)
    [void]$k::SetConsoleMode($h, [uint32](($mode -bor 0x80) -band (-bnot 0x40)))
}
