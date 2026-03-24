include $(TOPDIR)/rules.mk

PKG_NAME:=luci-app-jxnu-srun
PKG_VERSION:=0.0.0
PKG_RELEASE:=1

include $(INCLUDE_DIR)/package.mk

RUNTIME_DEPENDS:=+python3-light
LUCI_FILE_DEPENDS:=
LUCI_PACKAGE_DEPENDS:=+jxnu-srun $(LUCI_FILE_DEPENDS)
BUNDLE_DEPENDS:=$(RUNTIME_DEPENDS) $(LUCI_FILE_DEPENDS)

# ---------------------------------------------------------------------------
# Package: jxnu-srun (base CLI/TUI, no LuCI)
# ---------------------------------------------------------------------------

define Package/jxnu-srun
  SECTION:=net
  CATEGORY:=Network
  TITLE:=JXNU SRun campus network client (CLI/TUI)
  DEPENDS:=$(RUNTIME_DEPENDS)
  PKGARCH:=all
endef

define Package/jxnu-srun/description
  Automatic SRun authentication daemon for JXNU campus network.
  Includes CLI commands for status, login, logout, config, and log viewing.
endef

define Package/jxnu-srun/install
	$(INSTALL_DIR) $(1)/usr/lib/jxnu_srun
	$(INSTALL_DIR) $(1)/usr/lib/jxnu_srun/schools
	$(INSTALL_DIR) $(1)/etc/init.d
	$(INSTALL_DIR) $(1)/etc/uci-defaults
	$(CP) $(CURDIR)/root/usr/lib/jxnu_srun/*.py \
		$(1)/usr/lib/jxnu_srun/
	$(CP) $(CURDIR)/root/usr/lib/jxnu_srun/defaults.json \
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
  DEPENDS:=$(LUCI_PACKAGE_DEPENDS)
  CONFLICTS:=luci-app-jxnu-srun-bundle
  PKGARCH:=all
endef

define Package/luci-app-jxnu-srun/description
  LuCI web interface for the JXNU SRun campus network client.
  Requires the jxnu-srun runtime package.
endef

define Package/luci-app-jxnu-srun/install
	$(INSTALL_DIR) $(1)/usr/lib/lua/luci/controller
	$(INSTALL_DIR) $(1)/usr/lib/lua/luci/model/cbi
	$(CP) $(CURDIR)/root/usr/lib/lua/luci/controller/*.lua \
		$(1)/usr/lib/lua/luci/controller/
	$(CP) $(CURDIR)/root/usr/lib/lua/luci/model/cbi/*.lua \
		$(1)/usr/lib/lua/luci/model/cbi/
endef

define Package/luci-app-jxnu-srun-bundle
  SECTION:=luci
  CATEGORY:=LuCI
  SUBMENU:=3. Applications
  TITLE:=LuCI interface for JXNU SRun (bundle)
  DEPENDS:=$(BUNDLE_DEPENDS)
  CONFLICTS:=jxnu-srun luci-app-jxnu-srun
  PKGARCH:=all
endef

define Package/luci-app-jxnu-srun-bundle/description
  Self-contained package with both the JXNU SRun runtime and LuCI files
  for manual installation without the split package pair.
endef

define Package/luci-app-jxnu-srun-bundle/install
	$(INSTALL_DIR) $(1)/usr/lib/jxnu_srun
	$(INSTALL_DIR) $(1)/usr/lib/jxnu_srun/schools
	$(INSTALL_DIR) $(1)/etc/init.d
	$(INSTALL_DIR) $(1)/etc/uci-defaults
	$(CP) $(CURDIR)/root/usr/lib/jxnu_srun/*.py \
		$(1)/usr/lib/jxnu_srun/
	$(CP) $(CURDIR)/root/usr/lib/jxnu_srun/defaults.json \
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
$(eval $(call BuildPackage,luci-app-jxnu-srun-bundle))
