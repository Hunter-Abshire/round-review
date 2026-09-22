import type { MenuItemConstructorOptions } from 'electron';

/** A top-level menu. Electron allows a built Menu here; these templates never use one. */
export interface MenuSection extends MenuItemConstructorOptions {
  label: string;
  submenu: MenuItemConstructorOptions[];
}

/**
 * The application menu. Settings lives under File, where people look for it, and carries the
 * platform's usual shortcut.
 */
export const buildMenuTemplate = (
  onOpenSettings: () => void,
  platform: NodeJS.Platform,
): MenuSection[] => {
  const modifier = platform === 'darwin' ? 'Cmd' : 'Ctrl';
  return [
    {
      label: 'File',
      submenu: [
        {
          label: 'Settings',
          accelerator: `${modifier}+,`,
          click: () => onOpenSettings(),
        },
        { type: 'separator' },
        { role: 'quit' },
      ],
    },
    {
      label: 'View',
      submenu: [
        { role: 'reload' },
        { role: 'toggleDevTools' },
        { type: 'separator' },
        { role: 'resetZoom' },
        { role: 'zoomIn' },
        { role: 'zoomOut' },
        { type: 'separator' },
        { role: 'togglefullscreen' },
      ],
    },
  ];
};
