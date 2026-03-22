include $(TOPDIR)/rules.mk

PKG_NAME:=luci-app-jxnu-srun
PKG_VERSION:=0.0.0
PKG_RELEASE:=1

include $(INCLUDE_DIR)/package.mk

# ---------------------------------------------------------------------------
# Package: jxnu-srun (base CLI/TUI, no LuCI)
# ---------------------------------------------------------------------------

define Package/jxnu-srun
  SECTION:=net
  CATEGORY:=Network
  TITLE:=JXNU SRun campus network client (CLI/TUI)
  DEPENDS:=+python3-light
  PKGARCH:=all
endef

define Package/jxnu-srun/description
  Automatic SRun authentication daemon for JXNU campus network.
  Includes CLI commands for status, login, logout, config, and log viewing.
endef

define Package/jxnu-srun/postinst
#!/bin/sh
[ -n "$$IPKG_INSTROOT" ] || {
	chmod 0755 /etc/init.d/jxnu_srun 2>/dev/null
	chmod 0755 /usr/lib/jxnu_srun/client.py 2>/dev/null
}
exit 0
endef

define Package/jxnu-srun/install
	$(INSTALL_DIR) $(1)/usr/lib/jxnu_srun
	$(INSTALL_DIR) $(1)/usr/lib/jxnu_srun/schools
	$(INSTALL_DIR) $(1)/etc/init.d
	$(INSTALL_DIR) $(1)/etc/uci-defaults
	$(CP) $(CURDIR)/root/usr/lib/jxnu_srun/*.py \
		$(1)/usr/lib/jxnu_srun/
	$(CP) $(CURDIR)/root/usr/lib/jxnu_srun/schools/*.py \
		$(1)/usr/lib/jxnu_srun/schools/
	$(INSTALL_BIN) $(CURDIR)/root/etc/init.d/jxnu_srun \
		$(1)/etc/init.d/jxnu_srun
	$(if $(wildcard $(CURDIR)/root/etc/uci-defaults/*), \
		$(CP) $(CURDIR)/root/etc/uci-defaults/* \
			$(1)/etc/uci-defaults/)
	$(INSTALL_DIR) $(1)/usr/bin
	$(INSTALL_BIN) $(CURDIR)/root/usr/bin/srunnet \
		$(1)/usr/bin/srunnet
endef

# ---------------------------------------------------------------------------
# Package: luci-app-jxnu-srun (LuCI addon, depends on jxnu-srun)
# ---------------------------------------------------------------------------

define Package/luci-app-jxnu-srun
  SECTION:=luci
  CATEGORY:=LuCI
  SUBMENU:=3. Applications
  TITLE:=LuCI interface for JXNU SRun
  DEPENDS:=+jxnu-srun
  PKGARCH:=all
endef

define Package/luci-app-jxnu-srun/description
  LuCI web interface for the JXNU SRun campus network client.
  Requires the jxnu-srun base package.
endef

define Package/luci-app-jxnu-srun/install
	$(INSTALL_DIR) $(1)/usr/lib/lua/luci/controller
	$(INSTALL_DIR) $(1)/usr/lib/lua/luci/model/cbi
	$(CP) $(CURDIR)/root/usr/lib/lua/luci/controller/*.lua \
		$(1)/usr/lib/lua/luci/controller/
	$(CP) $(CURDIR)/root/usr/lib/lua/luci/model/cbi/*.lua \
		$(1)/usr/lib/lua/luci/model/cbi/
endef

# ---------------------------------------------------------------------------
# Build (nothing to compile for pure-Python/Lua packages)
# ---------------------------------------------------------------------------

define Build/Compile
endef

$(eval $(call BuildPackage,jxnu-srun))
$(eval $(call BuildPackage,luci-app-jxnu-srun))
