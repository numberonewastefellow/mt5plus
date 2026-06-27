Add-Type -AssemblyName System.Drawing

function New-XauIcon([int]$size, [string]$path) {
    $bmp = New-Object System.Drawing.Bitmap($size, $size)
    $g = [System.Drawing.Graphics]::FromImage($bmp)
    $g.SmoothingMode = [System.Drawing.Drawing2D.SmoothingMode]::AntiAlias
    $g.TextRenderingHint = [System.Drawing.Text.TextRenderingHint]::AntiAliasGridFit

    $rect = New-Object System.Drawing.Rectangle(0, 0, $size, $size)
    $brush = New-Object System.Drawing.Drawing2D.LinearGradientBrush(
        $rect,
        [System.Drawing.Color]::FromArgb(255, 251, 191, 36),
        [System.Drawing.Color]::FromArgb(255, 180, 120, 20),
        45.0
    )
    $g.FillRectangle($brush, $rect)

    $fontSize = [single]([Math]::Round($size * 0.34))
    $font = New-Object System.Drawing.Font('Arial Black', $fontSize, [System.Drawing.FontStyle]::Bold, [System.Drawing.GraphicsUnit]::Pixel)
    $sf = New-Object System.Drawing.StringFormat
    $sf.Alignment = [System.Drawing.StringAlignment]::Center
    $sf.LineAlignment = [System.Drawing.StringAlignment]::Center
    $sf.FormatFlags = [System.Drawing.StringFormatFlags]::NoWrap
    $rectF = New-Object System.Drawing.RectangleF(0, 0, $size, $size)
    $g.DrawString('XAU', $font, [System.Drawing.Brushes]::Black, $rectF, $sf)

    $bmp.Save($path, [System.Drawing.Imaging.ImageFormat]::Png)

    $font.Dispose()
    $brush.Dispose()
    $g.Dispose()
    $bmp.Dispose()
    Write-Host "Wrote $path"
}

$dir = Join-Path $PSScriptRoot 'icons'
New-Item -ItemType Directory -Force -Path $dir | Out-Null

New-XauIcon 180 (Join-Path $dir 'apple-touch-icon-180.png')
New-XauIcon 192 (Join-Path $dir 'icon-192.png')
New-XauIcon 512 (Join-Path $dir 'icon-512.png')
